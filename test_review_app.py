"""GTK smoke tests; requires a display, does not access GitHub or switch Git."""
import unittest
from unittest.mock import patch
from review_app import ReviewWindow, Gtk


class OverviewTests(unittest.TestCase):
    def setUp(self):
        with patch.object(ReviewWindow, 'refresh'):
            self.window = ReviewWindow()
        self.window.show_all()
        self.window.work = lambda task, done: done(task())
        self.metadata = dict(number=12, title='Example change', body='Description here',
                             url='https://github.com/plancraft/plancraft/pull/12',
                             state='OPEN', isDraft=True, baseRefName='main',
                             headRefName='feature', headRefOid='abc',
                             reviewDecision='REVIEW_REQUIRED', statusCheckRollup=[])

    def tearDown(self):
        self.window.hide()

    def labels(self):
        return [w.get_text() for w in self.window.canvas.get_children()[0].get_children()
                if isinstance(w, Gtk.Label)]

    def test_branch_selection_loads_overview_without_diff(self):
        w = self.window
        w.branch_options = ['origin/feature']
        w.filter_branches()
        with patch.object(w, 'github', side_effect=[[
            dict(number=12, state='OPEN', updatedAt='2026-01-01')], self.metadata]), \
                patch('review_app.git') as git, patch.object(w, 'open_review') as diff:
            w.choose_branch(w.branch_list, w.branch_list.get_row_at_index(0))
            git.assert_not_called()
            diff.assert_not_called()
            self.assertIn('Draft · Review required', self.labels())
            self.assertIn('Description here', self.labels())
            self.assertIsNot(w.actions.get_parent(), w.canvas)
            self.assertTrue(w.diff_button.get_mapped())
            w.diff_button.clicked()
            diff.assert_called_once()
        self.assertEqual(w.selector.get_visible_child_name(), 'branch')
        self.assertFalse(w.pr.get_child_visible())

    def test_chat_snapshot_includes_commit_file_and_selection(self):
        from diff_canvas import DiffCanvas
        w = self.window
        w.show_overview(('origin/feature', self.metadata))
        with self.assertRaises(ValueError):
            w.chat_context()
        file = ('M', 'src/a.ts', 'src/a.ts', '@@ -1 +1 @@\n-old\n+new')
        board = DiffCanvas([file])
        w.views.add_named(board, 'map'); board.show_all()
        w.views.set_visible_child_name('map')
        w.review_head = self.metadata['headRefOid']
        board.active_file = file
        text = next(child for child in board.cards[file[2]].get_children() if isinstance(child, Gtk.TextView))
        buffer = text.get_buffer()
        buffer.select_range(buffer.get_start_iter(), buffer.get_end_iter())
        context = w.chat_context()
        self.assertEqual(context['head'], 'abc')
        self.assertEqual(context['file'], 'src/a.ts')
        self.assertEqual(context['diff'], file[3])
        self.assertIn('+new', context['selection'])
        self.assertEqual(context['pr'], 12)
        board.active_file = None
        board.set_zoom(.5)
        attached = w.chat_context()
        self.assertEqual(attached['file'], 'src/a.ts')
        self.assertEqual(attached['selection'], context['selection'])
        self.assertEqual(attached['selection_diff_rows'], [1, 3])
        board.clear_selection()
        self.assertEqual(w.chat_context()['selection'], '')
        w.chat_busy(True)
        self.assertFalse(w.bar.get_sensitive())
        self.assertFalse(w.actions.get_sensitive())
        w.chat_busy(False)

    def test_map_button_returns_to_overview_without_loading_diff(self):
        w = self.window
        w.show_overview(('origin/feature', self.metadata))
        w.views.add_named(Gtk.Box(), 'map')
        w.views.get_child_by_name('map').show()
        w.views.set_visible_child_name('map')
        with patch.object(w, 'open_review') as load:
            w.diff_button.clicked()
            load.assert_not_called()
        self.assertEqual(w.views.get_visible_child_name(), 'overview')
        self.assertEqual(w.diff_button.get_label(), 'Open diff map')

    def test_overview_counts_have_separate_colors(self):
        self.metadata.update(changedFiles=49, additions=1515, deletions=323)
        self.window.show_overview(('origin/feature', self.metadata))
        panel = self.window.canvas.get_children()[0]
        stats = next(w for w in panel.get_children() if w.get_style_context().has_class('stats'))
        counts = stats.get_children()
        self.assertEqual([w.get_text() for w in counts], ['49 changed files', '+1,515 added', '−323 removed'])
        self.assertTrue(counts[1].get_style_context().has_class('stat-added'))
        self.assertTrue(counts[2].get_style_context().has_class('stat-removed'))
        while Gtk.events_pending(): Gtk.main_iteration()
        green = counts[1].get_style_context().get_color(Gtk.StateFlags.NORMAL)
        red = counts[2].get_style_context().get_color(Gtk.StateFlags.NORMAL)
        self.assertGreater(green.green, green.red)
        self.assertGreater(red.red, red.green)

    def test_no_pr_retains_comparison_choice(self):
        w = self.window
        w.branch.set_text('origin/no-pr')
        with patch.object(w, 'github', return_value=[]), patch('review_app.git') as git:
            w.load_overview()
            git.assert_not_called()
        self.assertIn('No pull request found for this branch.', self.labels())
        self.assertIsNotNone(w.base.get_parent())
        w.show_overview(('origin/no-pr', None))
        self.assertIsNotNone(w.base.get_parent())

    def test_explicit_pr_and_closed_state(self):
        w = self.window
        w.mode.set_active(1)
        w.pr.set_text('12')
        self.metadata.update(state='MERGED', isDraft=False)
        with patch.object(w, 'github', return_value=self.metadata) as gh:
            w.load_overview()
            self.assertEqual(gh.call_args.args[:2], ('view', '12'))
        self.assertIn('Merged · Review required', self.labels())
        self.assertEqual(w.selector.get_visible_child_name(), 'pr')


if __name__ == '__main__':
    unittest.main()
