import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from conversations import Conversations, connection, load_conversations, Gtk


class ConversationTests(unittest.TestCase):
    def test_pagination(self):
        queries = []
        def request(query):
            queries.append(query)
            return {'root': {'comments': {'nodes': [len(queries)], 'pageInfo': {
                'hasNextPage': len(queries) == 1, 'endCursor': 'next'}}}}
        self.assertEqual(list(connection('root: node(id:"pr")', 'comments', 'id', request)), [1, 2])
        self.assertIn('after: "next"', queries[1])

    def test_loads_general_reviews_and_thread_replies(self):
        comment = dict(id='c', body='Hello', createdAt='2026-01-01', url='https://github.com', author=None)
        def request(query):
            self.assertEqual(query.count('{'), query.count('}'))
            if 'repository(' in query:
                return {'repository': {'pullRequest': {'id': 'pr'}}}
            field = 'reviewThreads' if 'reviewThreads(' in query else 'reviews' if 'reviews(' in query else 'comments'
            nodes = [dict(id='thread', path='a.ts', line=3, originalLine=3,
                          isResolved=True, isOutdated=False)] if field == 'reviewThreads' else [comment]
            return {'root': {field: {'nodes': nodes, 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}
        with patch('conversations.graphql', side_effect=request):
            items = load_conversations(123)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[2]['title'], 'a.ts:3 · Resolved on GitHub')
        self.assertEqual(items[2]['comments'], [comment])

    def test_hide_unhide_persists_and_is_scoped_to_pr(self):
        item = dict(id='thread', title='a.ts:3', comments=[])
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'state.sqlite3'
            panel = Conversations(database)
            panel.number = 1
            panel.items = [item]
            panel.render()
            self.assertFalse(panel.rows.get_children()[0].get_children()[0].get_expanded())
            panel.set_hidden(item, True)
            group = panel.rows.get_children()[0]
            self.assertIsInstance(group, Gtk.Expander)
            self.assertEqual(group.get_label(), '1 conversations hidden')
            self.assertFalse(group.get_expanded())
            panel.destroy()
            panel = Conversations(database)
            panel.number = 1
            panel.items = [item]
            panel.render()
            self.assertIsInstance(panel.rows.get_children()[0], Gtk.Expander)
            panel.number = 2
            panel.render()
            self.assertIsInstance(panel.rows.get_children()[0], Gtk.Box)
            panel.number = 1
            panel.set_hidden(item, False)
            self.assertIsInstance(panel.rows.get_children()[0], Gtk.Box)
            panel.destroy()

    def test_stale_response_is_ignored_and_errors_are_visible(self):
        panel = Conversations(':memory:')
        panel.generation = 2
        panel.loaded(1, [{'id': 'old'}], None)
        self.assertEqual(panel.items, [])
        panel.loaded(2, [], 'offline')
        self.assertIn('offline', panel.message.get_text())
        panel.destroy()
        self.assertFalse(panel.loaded(2, [], None))


if __name__ == '__main__':
    unittest.main()
