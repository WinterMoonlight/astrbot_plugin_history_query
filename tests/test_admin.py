from __future__ import annotations

import asyncio
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from history_query.admin import AdminError, ConfigEditor, PLUGIN_NAME, defaults, normalize, revision
from history_query.config import Settings
from tests.support import Context, config, workspace


class EditorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=workspace();self.raw=config();self.context=Context(Path(self.temp.name)/'unused.sqlite3')
        self.writes=0;self.resets=0;self.closed=False
        async def persist(value):
            self.writes+=1;self.raw.update(copy.deepcopy(value));return True
        async def apply():self.resets+=1;return None
        self.editor=ConfigEditor(self.raw,self.context,'4.29.4',lambda:{'worker_state':'未启动'},persist,apply,lambda:self.closed)
    async def asyncTearDown(self):self.temp.cleanup()

    async def state(self):return await self.editor.get('admin',PLUGIN_NAME)

    async def test_dashboard_identity_and_plugin_scope_required(self):
        for username,plugin in [(None,PLUGIN_NAME),('',PLUGIN_NAME),('admin','other')]:
            with self.subTest(username=username,plugin=plugin),self.assertRaises(AdminError) as err:
                await self.editor.get(username,plugin)
            self.assertEqual(err.exception.status,403)
        with self.assertRaises(AdminError):await self.editor.save(None,PLUGIN_NAME,{})
        self.assertEqual(self.writes,0)

    async def test_state_does_not_read_history_or_invoke_platform(self):
        state=await self.state()
        self.assertEqual(state['revision'],revision(self.raw))
        self.assertFalse(self.context.calls)
        self.assertEqual(state['config']['readers'][0]['user_id'],'7')

    async def test_save_normalizes_and_applies_only_after_persist(self):
        state=await self.state();payload=copy.deepcopy(state['config']);payload['enabled']=False
        payload['aliases'][0]['names']=[' 英语群 ','英语群']
        saved=await self.editor.save('admin',PLUGIN_NAME,{'revision':state['revision'],'config':payload})
        self.assertTrue(saved['saved']);self.assertFalse(self.raw['enabled'])
        self.assertEqual(saved['config']['aliases'][0]['names'],['英语群'])
        self.assertEqual((self.writes,self.resets),(1,1))
        Settings.parse(self.raw)

    async def test_optimistic_concurrency_keeps_other_changes(self):
        first=await self.state();self.raw['audit_enabled']=False
        first['config']['enabled']=False
        with self.assertRaises(AdminError) as err:
            await self.editor.save('admin',PLUGIN_NAME,{'revision':first['revision'],'config':first['config']})
        self.assertEqual(err.exception.status,409);self.assertEqual(self.writes,0)
        self.assertFalse(self.raw['audit_enabled'])

    async def test_failed_native_save_restores_own_memory_change(self):
        before=copy.deepcopy(self.raw);state=await self.state();state['config']['enabled']=False
        async def failing(value):self.raw.update(value);raise OSError('disk failure')
        self.editor.persist=failing
        with self.assertRaises(AdminError) as err:
            await self.editor.save('admin',PLUGIN_NAME,{'revision':state['revision'],'config':state['config']})
        self.assertEqual(err.exception.status,500);self.assertEqual(self.raw,before);self.assertEqual(self.resets,0)

    async def test_failed_save_does_not_undo_newer_change(self):
        state=await self.state();state['config']['enabled']=False
        async def failing(value):self.raw.update(value);self.raw['audit_enabled']=False;raise OSError()
        self.editor.persist=failing
        with self.assertRaises(AdminError):await self.editor.save('admin',PLUGIN_NAME,{'revision':state['revision'],'config':state['config']})
        self.assertFalse(self.raw['audit_enabled'])

    async def test_noop_does_not_reset_running_queries(self):
        state=await self.state()
        result=await self.editor.save('admin',PLUGIN_NAME,{'revision':state['revision'],'config':state['config']})
        self.assertFalse(result['saved']);self.assertEqual((self.writes,self.resets),(0,0))

    async def test_rejects_unknown_fields_bad_sources_and_limits(self):
        for change in ['unknown','source','limit','template']:
            state=await self.state();cfg=state['config']
            if change=='unknown':cfg['database_path']='/private'
            if change=='source':cfg['readers'][0]['groups']=['other:GroupMessage:100']
            if change=='limit':cfg['limits']['scan']=1000000
            if change=='template':cfg['readers'][0]['__template_key']='untrusted'
            with self.subTest(change=change),self.assertRaises(AdminError):
                await self.editor.save('admin',PLUGIN_NAME,{'revision':state['revision'],'config':cfg})
        self.assertEqual(self.writes,0)

    async def test_identify_and_groups_use_only_metadata_apis(self):
        identity=await self.editor.identify('admin',PLUGIN_NAME,'qq-a')
        self.assertEqual(identity['bot_id'],'900')
        catalog=await self.editor.groups('admin',PLUGIN_NAME,'qq-a')
        self.assertEqual(len(catalog['groups']),2)
        self.assertTrue(all(method in {'get_login_info','get_group_list'} for _,method in self.context.calls))
        self.context.offline=True
        with self.assertRaises(AdminError):await self.editor.groups('admin',PLUGIN_NAME,'qq-a')

    async def test_unknown_instance_and_closed_editor(self):
        with self.assertRaises(AdminError):await self.editor.groups('admin',PLUGIN_NAME,'not-bound')
        self.closed=True
        with self.assertRaises(AdminError) as err:await self.state()
        self.assertEqual(err.exception.status,503)

    async def test_simultaneous_saves_only_one_revision_wins(self):
        state=await self.state();a=copy.deepcopy(state['config']);b=copy.deepcopy(a)
        a['enabled']=False;b['audit_enabled']=False
        replies=await asyncio.gather(*[self.editor.save('admin',PLUGIN_NAME,{'revision':state['revision'],'config':v}) for v in (a,b)],return_exceptions=True)
        self.assertEqual(sum(isinstance(r,AdminError) for r in replies),1)
        self.assertEqual(self.writes,1)

    async def test_payload_size_and_defaults(self):
        self.assertFalse(Settings.parse(defaults()).enabled)
        cfg=defaults();cfg['aliases']=[{'source':'x','names':['x'*300000]}]
        with self.assertRaises(AdminError) as err:normalize(cfg)
        self.assertEqual(err.exception.status,413)

    async def test_private_recent_setting_round_trip_preserves_permissions(self):
        state = await self.state()
        self.assertEqual(state['config']['limits']['private_recent_scan'], 50)
        self.assertEqual(state['limits']['private_recent_scan'], [1, 2000])
        readers = copy.deepcopy(state['config']['readers'])
        state['config']['limits']['private_recent_scan'] = 75
        saved = await self.editor.save('admin', PLUGIN_NAME,
            {'revision': state['revision'], 'config': state['config']})
        self.assertEqual(saved['config']['limits']['private_recent_scan'], 75)
        self.assertEqual((await self.state())['config']['readers'], readers)
        self.assertEqual(self.resets, 1)
