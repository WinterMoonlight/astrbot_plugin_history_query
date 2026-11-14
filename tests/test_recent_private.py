"""Synthetic recent-history windows and authorized private directory tests."""
import json
import unittest

from tests import test_service as support


class RecentPrivateTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.ServiceTests.asyncSetUp
    asyncTearDown = support.ServiceTests.asyncTearDown
    make_turn = support.ServiceTests.make_turn
    call = support.ServiceTests.call
    source = support.ServiceTests.source
    write = support.ServiceTests.write

    def private(self, peer, number=100, rows=3, platform='qq-a', updated='2026-11-14 01:00:00'):
        content = [{'role': 'user', 'content': f'synthetic-{peer}-{i:03}'} for i in range(rows)]
        self.write(lambda db: db.execute('INSERT INTO conversations VALUES (?,?,?,?,?,?)',
            (number, f'doc-{number}', platform, f'{platform}:FriendMessage:{peer}', json.dumps(content), updated)))

    async def test_catalog_lists_existing_authorized_sessions_not_all_contacts(self):
        self.private('12')
        self.raw['readers'][0]['private_sessions'].append('qq-a:FriendMessage:99')
        result = await self.call('find', {'list_private': True})
        self.assertEqual(result['status'], 'private_sessions', result)
        self.assertEqual([r['peer'] for r in result['candidates']], ['11'])
        self.assertEqual(result['candidates'][0]['name'], '小王')
        self.assertNotIn('text', result['candidates'][0])
        self.assertIn('不是最后来信时间', result['time_notice'])
        self.assertFalse(result['has_more'])
        self.assertTrue(self.turn.locked)

    async def test_catalog_empty_permissions_never_open_database(self):
        self.raw['readers'][0]['private_sessions'] = []
        result = await self.call('find', {'list_private': True})
        self.assertEqual(result['candidates'], [])
        self.assertFalse(self.context.calls)
        self.assertEqual(self.service.worker.starts, 0)

    async def test_catalog_requires_query_identity(self):
        event, turn = self.make_turn()
        event.user = '999'
        result = await self.call('find', {'list_private': True}, event, turn)
        self.assertEqual(result['status'], 'denied')
        self.assertFalse(self.context.calls)

    async def test_catalog_cursor_is_stable_under_insert_and_delete(self):
        self.raw['readers'][0]['all_private_on'] = ['qq-a']
        for index, peer in enumerate(range(20, 28)):
            self.private(str(peer), 100 + index)
        first = await self.call('find', {'list_private': True})
        self.assertEqual(len(first['candidates']), 5)
        self.assertTrue(first['has_more'])
        self.private('29', 300)
        self.write(lambda db: db.execute('DELETE FROM conversations WHERE user_id=?', ('qq-a:FriendMessage:24',)))
        second = await self.call('find', {'catalog_cursor': first['catalog_cursor']})
        peers = [r['peer'] for r in first['candidates'] + second['candidates']]
        self.assertEqual(len(peers), len(set(peers)))
        self.assertNotIn('29', peers)
        self.assertNotIn('24', peers)
        self.assertFalse(second['has_more'])

    async def test_catalog_rechecks_permissions_on_continuation(self):
        self.raw['readers'][0]['all_private_on'] = ['qq-a']
        for index, peer in enumerate(range(20, 28)):
            self.private(str(peer), 100 + index)
        first = await self.call('find', {'list_private': True})
        self.raw['readers'][0]['all_private_on'] = []
        second = await self.call('find', {'catalog_cursor': first['catalog_cursor']})
        self.assertEqual(second['status'], 'denied')
        event, turn = self.make_turn(text='继续列出')
        fresh = await self.call('find', {'catalog_cursor': first['catalog_cursor']}, event, turn)
        self.assertEqual(fresh['candidates'], [])

    async def test_catalog_cursor_cannot_be_used_by_other_authorized_user(self):
        self.raw['readers'][0]['all_private_on'] = ['qq-a']
        for index, peer in enumerate(range(20, 26)):
            self.private(str(peer), 100 + index)
        first = await self.call('find', {'list_private': True})
        self.raw['readers'].append({**self.raw['readers'][0], 'user_id': '8'})
        event, turn = self.make_turn(user='8')
        result = await self.call('find', {'catalog_cursor': first['catalog_cursor']}, event, turn)
        self.assertEqual(result['status'], 'invalid_reference')

    async def test_catalog_keeps_same_number_on_different_platforms_separate(self):
        self.raw['readers'][0]['private_sessions'].append('qq-b:FriendMessage:11')
        self.private('11', 200, platform='qq-b')
        first = await self.call('find', {'list_private': True})
        second = await self.call('find', {'catalog_cursor': first['catalog_cursor']})
        self.assertEqual([(r['platform'], r['peer']) for r in first['candidates'] + second['candidates']],
                         [('qq-a', '11'), ('qq-b', '11')])
        self.assertNotEqual(first['candidates'][0]['source_ref'], second['candidates'][0]['source_ref'])

    async def test_catalog_does_not_expand_latched_query_scope(self):
        self.raw['readers'][0]['all_private_on'] = ['qq-a']
        self.private('12')
        source = await self.source('11')
        await self.call('search', {'source_refs': [source]})
        result = await self.call('find', {'list_private': True})
        self.assertEqual([r['peer'] for r in result['candidates']], ['11'])

    async def test_catalog_reference_can_read_selected_private_without_date_question(self):
        result = await self.call('find', {'kind': 'private'})
        ref = result['candidates'][0]['source_ref']
        answer = await self.call('search', {'source_refs': [ref], 'start_time': '2026-11-13', 'end_time': '2026-11-14'})
        self.assertTrue(answer['records'], answer)
        self.assertIn('未按日期筛选', answer['time_notice'])
        self.assertNotIn('confirmation_ref', answer)

    async def test_recent_window_is_50_per_private_and_per_turn(self):
        self.write(lambda db: db.execute('DELETE FROM conversations'))
        self.private('11', rows=130)
        source = await self.source('11')
        args = {'source_refs': [source], 'keywords': ['absent'], 'start_time': 'yesterday'}
        first = await self.call('search', args)
        self.assertEqual(first['coverage'][0]['scanned'], 50, first)
        self.assertEqual(first['coverage'][0]['private_allowance_left'], 0)
        self.assertEqual(self.turn.rows_left, 450)
        repeat = await self.call('search', {'continuation': first['continuation']})
        self.assertEqual(repeat['coverage'][0]['scanned'], 50)
        self.assertEqual(self.turn.rows_left, 450)
        event, turn = self.make_turn(text='继续')
        second = await self.call('search', {'continuation': first['continuation']}, event, turn)
        self.assertEqual(second['coverage'][0]['scanned'], 100)
        self.assertEqual(turn.rows_left, 450)

    async def test_recent_reads_from_tail_and_older_match_waits_for_continuation(self):
        self.write(lambda db: db.execute('DELETE FROM conversations'))
        self.private('11', rows=100)
        source = await self.source('11')
        first = await self.call('search', {'source_refs': [source], 'limit': 2})
        self.assertEqual([r['text'] for r in first['records']], ['synthetic-11-099', 'synthetic-11-098'])
        event, turn = self.make_turn(text='查旧内容')
        missing = await self.call('search', {'source_refs': [source], 'keywords': ['synthetic-11-010']}, event, turn)
        self.assertEqual(missing['records'], [])
        event, turn = self.make_turn(text='继续')
        older = await self.call('search', {'continuation': missing['continuation']}, event, turn)
        self.assertEqual(older['records'][0]['text'], 'synthetic-11-010')

    async def test_recent_prefers_updated_old_conversation_not_largest_id(self):
        self.write(lambda db: db.execute('UPDATE conversations SET updated_at=? WHERE conversation_id=?',
                                       ('2026-11-14 11:00:00', 'c0')))
        source = await self.source('11')
        result = await self.call('search', {'source_refs': [source], 'limit': 2})
        self.assertTrue(all(r['conversation_id'] == 'c0' for r in result['records']))

    async def test_two_private_windows_share_global_budget_fairly(self):
        self.write(lambda db: db.execute('DELETE FROM conversations'))
        self.private('11', rows=100)
        self.private('12', number=101, rows=100)
        self.raw['readers'][0]['private_sessions'].append('qq-a:FriendMessage:12')
        found = await self.call('find', {'names': ['11', '12']})
        result = await self.call('search', {'source_refs': found['source_refs'], 'keywords': ['absent']})
        self.assertEqual([r['scanned'] for r in result['coverage']], [50, 50])
        self.assertEqual(self.turn.rows_left, 400)

    async def test_continuation_excludes_new_documents_and_detects_active_edits(self):
        self.write(lambda db: db.execute('DELETE FROM conversations'))
        self.private('11', rows=10)
        source = await self.source('11')
        first = await self.call('search', {'source_refs': [source], 'limit': 1})
        self.private('11', number=200, rows=1, updated='2026-11-14 11:00:00')
        event, turn = self.make_turn(text='继续')
        second = await self.call('search', {'continuation': first['continuation'], 'limit': 1}, event, turn)
        self.assertEqual(second['records'][0]['conversation_id'], 'doc-100')
        self.write(lambda db: db.execute("UPDATE conversations SET content=replace(content,'synthetic','rewritten') WHERE conversation_id='doc-100'"))
        result = await self.call('search', {'continuation': first['continuation']}, event, turn)
        self.assertEqual(result['issue']['code'], 'stale', result)

    async def test_mixed_query_keeps_group_dates_but_reads_recent_private(self):
        private, group = await self.source('11'), await self.source('英语群')
        result = await self.call('search', {'source_refs': [private, group],
            'start_time': '2026-11-13', 'end_time': '2026-11-14'})
        self.assertTrue(result['records'], result)
        self.assertTrue(all(r['data_type'] == 'FriendMessage' for r in result['records']))
        self.assertEqual(result['coverage'][0]['read_mode'], 'recent_saved_records')
        self.assertEqual(result['coverage'][1]['read_mode'], 'date_filtered')
