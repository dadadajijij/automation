from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi import HTTPException
from hermes_http_gateway import db, main
from test_gateway import _make_settings, _init_temp_db


class AdminBrowseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = _make_settings(Path(self.tmp.name))
        _init_temp_db(self.settings)
        for user in ['alice', 'bob', 'empty']:
            db.upsert_user(self.settings, user, 'default')
        for session, user, title in [('a', 'alice', 'Alpha'), ('b', 'bob', 'Beta'),
                                      ('c', 'alice', 'Other'), ('title', 'bob', '超时处理')]:
            db.create_conversation(self.settings, external_session_id=session,
                                   user_id=user, hermes_profile='default', title=title)
        for session, user, content in [('a', 'alice', '超时 one'), ('a', 'alice', '超时 two'),
                                        ('b', 'bob', '超时 three'), ('c', 'alice', 'irrelevant')]:
            db.append_message(self.settings, external_session_id=session, user_id=user,
                              role='user', kind='chat', content=content, title='')

    def browse(self, **kwargs):
        return db.search_admin_context(self.settings, **kwargs)

    def test_content_search_links_users_and_unique_conversations(self):
        data = self.browse(query='超时')
        self.assertEqual({u['user_id'] for u in data['users']}, {'alice', 'bob'})
        self.assertEqual({c['external_session_id'] for c in data['conversations']}, {'a', 'b', 'title'})
        self.assertEqual(data['total_conversations'], 3)
        counts = {c['external_session_id']: c['match_count'] for c in data['conversations']}
        self.assertEqual(counts, {'a': 2, 'b': 1, 'title': 0})
        alice = next(u for u in data['users'] if u['user_id'] == 'alice')
        self.assertEqual(alice['conversation_count'], 1)
        self.assertEqual(alice['message_count'], 2)

    def test_user_search_defaults_first_and_switch_preserves_user_list(self):
        data = self.browse(user_query='default')
        first = data['users'][0]['user_id']
        self.assertEqual(data['scoped_user'], first)
        self.assertTrue(all(c['user_id'] == first for c in data['conversations']))
        switched = self.browse(user_query='default', user_id='alice')
        self.assertEqual(len(switched['users']), 3)
        self.assertEqual({c['external_session_id'] for c in switched['conversations']}, {'a', 'c'})

    def test_combined_filters_and_empty_user(self):
        data = self.browse(query='超时', user_query='ali')
        self.assertEqual([u['user_id'] for u in data['users']], ['alice'])
        self.assertEqual([c['external_session_id'] for c in data['conversations']], ['a'])
        empty = self.browse(user_query='empty')
        self.assertEqual(empty['scoped_user'], 'empty')
        self.assertEqual(empty['conversations'], [])
        missing = self.browse(query='does-not-exist', user_id='alice')
        self.assertEqual(missing['users'], [])
        self.assertEqual(missing['conversations'], [])

    def test_pagination_keeps_users_outside_current_page(self):
        data = self.browse(query='超时', limit=1)
        self.assertEqual(len(data['users']), 2)
        self.assertEqual(len(data['conversations']), 1)
        self.assertTrue(data['pagination']['has_more'])
        self.assertEqual(data['total_conversations'], 3)
        last = self.browse(query='超时', limit=1, offset=2)
        self.assertFalse(last['pagination']['has_more'])
        self.assertNotEqual(last['conversations'][0]['external_session_id'],
                            data['conversations'][0]['external_session_id'])

    def test_id_and_title_search(self):
        self.assertEqual([c['external_session_id'] for c in self.browse(query='Alpha')['conversations']], ['a'])
        self.assertEqual([c['external_session_id'] for c in self.browse(query='title')['conversations']], ['title'])

    def test_route_authentication_and_pagination_validation(self):
        request = SimpleNamespace(cookies={}, app=SimpleNamespace(state=SimpleNamespace(settings=self.settings)))
        with self.assertRaises(HTTPException) as ctx:
            main.admin_browse(request)
        self.assertEqual(ctx.exception.status_code, 401)
        session = db.create_admin_session(self.settings, username='admin', ttl_seconds=3600)
        request.cookies[self.settings.admin_cookie_name] = session['session_token']
        self.assertEqual(main.admin_browse(request, keyword='超时')['total_conversations'], 3)
        for kwargs in [{'limit': 0}, {'limit': 501}, {'offset': -1}]:
            with self.assertRaises(HTTPException) as ctx:
                main.admin_browse(request, **kwargs)
            self.assertEqual(ctx.exception.status_code, 422)
