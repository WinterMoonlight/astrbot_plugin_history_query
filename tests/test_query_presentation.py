"""Synthetic regressions for private names, scope confirmation and result meaning."""
from __future__ import annotations

import asyncio
import json
import unittest

from history_query.config import Settings, Source
from tests import test_service as service_test_support
from tests.support import add_group


class QueryPresentationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = service_test_support.ServiceTests.asyncSetUp
    asyncTearDown = service_test_support.ServiceTests.asyncTearDown
    make_turn = service_test_support.ServiceTests.make_turn
    call = service_test_support.ServiceTests.call
    source = service_test_support.ServiceTests.source
    write = service_test_support.ServiceTests.write

    def remove_private_alias(self):
        self.raw['aliases'] = [a for a in self.raw['aliases'] if 'FriendMessage' not in a['source']]

    def second_private(self):
        self.raw['readers'][0]['private_sessions'].append('qq-a:FriendMessage:12')
        rows = [{'role': 'user', 'content': f'第二个会话 synthetic {i}'} for i in range(20)]
        self.write(lambda db: db.execute('INSERT INTO conversations VALUES (?,?,?,?,?,?)',
            (100, 'second', 'qq-a', 'qq-a:FriendMessage:12', json.dumps(rows), '2026-11-14 01:00:00')))

    def profile_handler(self, handler):
        original = self.context.get_platform_inst
        def adapter(instance):
            value = original(instance)
            delegate = value.bot.call_action
            async def action(method, **kwargs):
                if method == 'get_stranger_info':
                    return await handler(instance, **kwargs)
                return await delegate(method, **kwargs)
            value.bot.call_action = action
            return value
        self.context.get_platform_inst = adapter

    async def test_private_nickname_reaches_search_context_and_sender_filter(self):
        self.remove_private_alias()
        self.context.nicknames['qq-a', '11'] = '昵称甲'
        source = await self.source('11')
        result = await self.call('search', {'source_refs': [source], 'sender': '昵称甲'})
        self.assertTrue(result['records'], result)
        self.assertTrue(all(r['sender_name'] == '昵称甲' and r['role'] == 'user' for r in result['records']))
        self.assertTrue(all(r['sender_display'] == '昵称甲（QQ 11）' for r in result['records']))
        context = await self.call('context', {'record_ref': result['records'][0]['record_ref'], 'radius': 1})
        self.assertTrue(any(r['sender_name'] == '昵称甲' for r in context['records']))
        self.assertEqual(self.context.profile_requests, [('qq-a', '11')])

    async def test_configured_alias_avoids_profile_request(self):
        source = await self.source('小王')
        result = await self.call('search', {'source_refs': [source]})
        self.assertTrue(any(r['sender_display'] == '小王（QQ 11）' for r in result['records']))
        self.assertFalse(self.context.profile_requests)

    async def test_unknown_nickname_falls_back_without_inventing_one(self):
        self.remove_private_alias()
        source = await self.source('11')
        result = await self.call('search', {'source_refs': [source]})
        user = next(r for r in result['records'] if r['role'] == 'user')
        self.assertEqual((user['sender_name'], user['sender_display'], user['sender_name_basis']),
                         ('', 'QQ 11', 'id_only'))
        self.assertEqual(len(self.context.profile_requests), 1)  # Negative result cached too.

    async def test_wrong_profile_identity_is_not_used(self):
        self.remove_private_alias()
        async def wrong(instance, **kwargs): return {'user_id': '999', 'nickname': '错误身份'}
        self.profile_handler(wrong)
        source = await self.source('11')
        result = await self.call('search', {'source_refs': [source]})
        self.assertNotIn('错误身份', json.dumps(result, ensure_ascii=False))

    async def test_profile_failure_and_timeout_do_not_block_history(self):
        self.remove_private_alias()
        for timeout in (False, True):
            self.service.metadata.names.clear()
            async def broken(instance, **kwargs):
                if timeout: await asyncio.sleep(2)
                raise ConnectionError('synthetic profile error')
            self.profile_handler(broken)
            source = await self.source('11')
            result = await self.call('search', {'source_refs': [source]})
            self.assertTrue(result['records'], result)

    async def test_name_lookup_uses_shared_execution_budget(self):
        self.remove_private_alias()
        async def slow(instance, **kwargs): await asyncio.sleep(2)
        self.profile_handler(slow)
        self.turn.execution_left = 0.02
        result = await self.call('find', {'name': '11'})
        self.assertEqual(result['status'], 'timeout', result)
        self.assertEqual(self.turn.execution_left, 0)
        self.assertEqual(self.service.worker.starts, 0)

    async def test_name_cache_does_not_mix_platforms_or_bot_bindings(self):
        metadata = self.service.metadata
        self.context.nicknames.update({('qq-a', '11'): '甲', ('qq-b', '11'): '乙'})
        a, b = Source.parse('qq-a:FriendMessage:11'), Source.parse('qq-b:FriendMessage:11')
        self.assertEqual((await metadata.private_name(a))[0], '甲')
        self.assertEqual((await metadata.private_name(b))[0], '乙')
        self.raw['instances'][0]['bot_id'] = '902'
        metadata.settings = Settings.parse(self.raw)
        self.context.nicknames['qq-a', '11'] = '新账号名称'
        self.assertEqual((await metadata.private_name(a))[0], '新账号名称')
        self.assertEqual(len(metadata.names), 3)

    async def test_expired_profile_cache_refreshes_even_with_old_reference(self):
        self.remove_private_alias()
        self.context.nicknames['qq-a', '11'] = '旧昵称'
        source = await self.source('11')
        key = ('qq-a', '900', '11')
        self.service.metadata.names[key] = (0, '旧昵称', 'current_qq_nickname')
        self.context.nicknames['qq-a', '11'] = '新昵称'
        result = await self.call('search', {'source_refs': [source]})
        self.assertTrue(any(r['sender_name'] == '新昵称' for r in result['records']))

    async def test_no_profile_metadata_for_unauthorized_target(self):
        result = await self.call('find', {'name': '12', 'kind': 'private'})
        self.assertEqual(result['status'], 'not_found')
        self.assertFalse(self.context.profile_requests)
        event, turn = self.make_turn()
        self.raw['readers'] = []
        result = await self.call('find', {'name': '11'}, event, turn)
        self.assertEqual(result['status'], 'denied')
        self.assertFalse(self.context.profile_requests)

    async def test_unknown_time_stays_unknown_even_with_date_in_text(self):
        self.write(lambda db: db.execute('UPDATE conversations SET content=? WHERE conversation_id=?',
            (json.dumps([{'role': 'user', 'content': '2026-11-14 21:22 明天交作业'}]), 'c2')))
        source = await self.source('小王')
        result = await self.call('search', {'source_refs': [source]})
        self.assertIn('时间信息不足', result['time_notice'])
        self.assertTrue(all(r['time'] is None and r['time_basis'] == 'unknown' for r in result['records']))
        self.assertIsNone(result['coverage'][0]['range'])
        context = await self.call('context', {'record_ref': result['records'][0]['record_ref'], 'radius': 1})
        self.assertTrue(all('无法确认日期' in r['time_display'] for r in context['records']))
        self.assertIn('不等于发送时间', context['time_notice'])

    async def test_budget_is_not_an_unread_record_count(self):
        source = await self.source()
        result = await self.call('search', {'source_refs': [source], 'limit': 2})
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('remaining', result)
        self.assertGreater(result['query_budget']['scan_allowance_left'], 0)
        self.assertIsNone(result['unread_record_count'])
        self.assertIn('不是未读记录数量', result['query_budget']['meaning'])
        self.assertIn('未读总数未知', result['message'])

    async def test_two_private_targets_confirmation_and_continuation_keep_scope(self):
        self.second_private()
        found = await self.call('find', {'names': ['11', '12'], 'kind': 'private'})
        pending = await self.call('search', {'source_refs': found['source_refs'],
            'start_time': '2026-11-13', 'end_time': '2026-11-14'})
        self.assertEqual(pending['status'], 'time_unknown')
        request = pending['next_call_after_user_confirms']
        self.assertEqual((await self.call('search', request))['status'], 'ask_user')
        event, turn = self.make_turn(text='同意不限定日期查询这两个会话')
        result = await self.call('search', request, event, turn)
        self.assertEqual(len(result['coverage']), 2, result)
        self.assertTrue(all(c['scanned'] > 0 for c in result['coverage']))
        event, turn = self.make_turn(text='继续')
        more = await self.call('search', {'continuation': result['continuation']}, event, turn)
        self.assertEqual(len(more['coverage']), 2, more)
        self.assertNotIn(more['status'], {'ask_user', 'time_unknown'})

    async def test_confirmation_cannot_change_filters_or_reuse_revoked_scope(self):
        self.second_private()
        found = await self.call('find', {'names': ['11', '12']})
        pending = await self.call('search', {'source_refs': found['source_refs'], 'start_time': '2026-11-13'})
        event, turn = self.make_turn(text='同意')
        request = pending['next_call_after_user_confirms']
        changed = await self.call('search', {**request, 'keywords': ['changed']}, event, turn)
        self.assertEqual(changed['status'], 'invalid_request')
        self.raw['readers'][0]['private_sessions'].remove('qq-a:FriendMessage:12')
        denied = await self.call('search', request, event, turn)
        self.assertEqual(denied['status'], 'denied', denied)
        self.assertNotIn('records', denied)

    async def test_collective_confirmation_restores_second_target_if_model_omits_it(self):
        self.second_private()
        found = await self.call('find', {'names': ['11', '12']})
        await self.call('search', {'source_refs': found['source_refs'], 'start_time': '2026-11-13'})
        event, turn = self.make_turn(text='同意不限定日期查询这两个会话')
        result = await self.call('search', {'source_refs': found['source_refs'][:1], 'all_dates': True}, event, turn)
        self.assertEqual(len(result['coverage']), 2, result)
        event, turn = self.make_turn(text='只查11，不限定日期')
        narrower = await self.call('search', {'source_refs': found['source_refs'][:1], 'all_dates': True}, event, turn)
        self.assertEqual(len(narrower['coverage']), 1, narrower)

    async def test_confirmation_requires_actual_user_consent(self):
        source = await self.source('小王')
        pending = await self.call('search', {'source_refs': [source], 'start_time': '2026-11-13'})
        for text in ['先别查了', '不同意不限定日期查询']:
            event, turn = self.make_turn(text=text)
            result = await self.call('search', pending['next_call_after_user_confirms'], event, turn)
            self.assertEqual(result['status'], 'ask_user')
        self.assertEqual(self.service.worker.starts, 0)

    async def test_pre_resolved_targets_may_be_queried_in_separate_calls(self):
        self.write(lambda db: add_group(db, 200, '学院通知 synthetic'))
        a, b = await self.source(), await self.source('学院群')
        first = await self.call('search', {'source_refs': [a], 'start_time': '2026-11-14'})
        second = await self.call('search', {'source_refs': [b], 'start_time': '2026-11-14'})
        self.assertTrue(first['records'])
        self.assertTrue(second['records'], second)
        self.assertEqual(len(self.turn.scope[0]), 2)
        late = await self.call('find', {'name': '小王'})
        self.assertEqual(late['status'], 'not_found')

    async def test_batch_ambiguity_never_selects_a_source_silently(self):
        self.context.groups['qq-a']['200'] = '英语考试'
        result = await self.call('find', {'names': ['100', '英语']})
        self.assertEqual(result['status'], 'ambiguous')
        self.assertNotIn('source_ref', json.dumps(result))
        self.assertFalse(self.service.worker.starts)
