"""GTK smoke tests; requires a display, does not access GitHub or switch Git."""
import unittest
from unittest.mock import patch
from review_app import ReviewWindow, Gtk


class OverviewTests(unittest.TestCase):
    def test_v_toggles_focused_file_but_not_while_typing(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from review_app import Gdk
        board = SimpleNamespace(active_file=('M', 'a', 'a', ''), sticky_viewed=Gtk.CheckButton())
        state = Mock()
        state.views.get_visible_child_name.return_value = 'map'
        state.views.get_child_by_name.return_value = board
        state.get_focus.return_value = None
        event = SimpleNamespace(keyval=Gdk.KEY_v, state=0)
        self.assertTrue(ReviewWindow.review_key(state, None, event))
        self.assertTrue(board.sticky_viewed.get_active())
        self.assertTrue(ReviewWindow.review_key(state, None, event))
        self.assertFalse(board.sticky_viewed.get_active())
        for focus in (Gtk.Entry(), Gtk.TextView()):
            state.get_focus.return_value = focus
            self.assertFalse(ReviewWindow.review_key(state, None, event))
        state.get_focus.return_value = None
        event.state = Gdk.ModifierType.CONTROL_MASK
        self.assertFalse(ReviewWindow.review_key(state, None, event))
        board.sticky_viewed.destroy()

    def test_dependency_escape_and_review_reset_clear_focus(self):
        from diff_canvas import DiffCanvas
        from review_app import Gdk
        from types import SimpleNamespace
        board = DiffCanvas([('M', 'a.ts', 'a.ts', '')])
        self.window.views.add_named(board, 'map')
        board.show_all()
        self.window.views.set_visible_child_name('map')
        board.dependencies.references.set_active(True)
        board.dependencies.select('a.ts')
        self.assertTrue(self.window.review_key(None, SimpleNamespace(keyval=Gdk.KEY_Escape, state=0)))
        self.assertIsNone(board.dependencies.selected)
        board.dependencies.select('a.ts')
        self.window.clear_canvas()
        self.assertIsNone(board.dependencies.selected)
        self.assertFalse(board.dependencies.loaded)
        board.destroy()

    def test_dependency_source_requires_matching_clean_commit(self):
        self.window.review_head = 'abc'
        with patch('review_git.ReviewGit') as repository, patch.object(self.window, 'open_file_in_zed') as open_file:
            repository.return_value.resolve_ref.return_value = 'abc'
            self.window.open_dependency_source('abc', 'src/a.ts', 12, 3)
            repository.return_value.ensure_clean.assert_called_once()
            open_file.assert_called_once_with('src/a.ts', 12, 3)
            open_file.reset_mock()
            repository.return_value.resolve_ref.return_value = 'other'
            with self.assertRaisesRegex(ValueError, 'checkout changed'):
                self.window.open_dependency_source('abc', 'src/a.ts', 12, 3)
            open_file.assert_not_called()

    def test_dependency_open_uses_line_and_rejects_paths_outside_clone(self):
        from pathlib import Path
        root = Path(self.state_directory.name) / 'review'
        root.mkdir()
        (root / 'a.ts').write_text('export {}')
        with patch('review_app.ROOT', root), patch('review_app.Gio.Subprocess.new') as launch:
            self.window.open_file_in_zed('a.ts', 12, 3)
            self.assertEqual(launch.call_args.args[0], ['zed', str(root), str((root / 'a.ts').resolve()) + ':12:3'])
            launch.reset_mock()
            self.window.open_file_in_zed('../outside.ts', 1, 1)
            launch.assert_not_called()

    def wait_until(self, condition):
        import time
        from gi.repository import GLib
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(.002)
        self.assertTrue(condition())

    def build_fixture(self):
        import threading
        from unittest.mock import Mock
        from test_dependency_overlay import data, A
        result = dict(merge='base', head='abc', files=[('M', A, A, '@@ -1 +1 @@\n-old\n+new')],
                      file_commits={A: 'abc'}, links=data(), links_error=None)
        job = Mock()
        job.dependency.cancelled = threading.Event()
        job.cancel.side_effect = job.dependency.cancelled.set
        def check():
            if job.dependency.cancelled.is_set():
                raise RuntimeError('Build cancelled.')
        job.check_cancelled.side_effect = check
        return job, result

    def test_open_map_shows_live_steps_then_uses_automatic_link_result(self):
        import threading
        w = self.window
        w.show_overview(('origin/feature', self.metadata))
        job, result = self.build_fixture()
        release = threading.Event()
        self.addCleanup(release.set)
        def run(progress):
            progress(0, 'Checking out abc…')
            progress(1, 'Installing packages…')
            release.wait(2)
            progress(2, '1 / 1 files', 1)
            progress(3, 'Finding links…')
            progress(4, 'Preparing file cards…')
            return result
        job.run.side_effect = run
        with patch('ReviewBuild.ReviewBuild', return_value=job) as constructor:
            w.open_review()
            self.wait_until(lambda: w.build_progress.stage == 1)
            self.assertEqual(w.views.get_visible_child_name(), 'loading')
            self.assertEqual(w.build_progress.detail.get_text(), 'Installing packages…')
            self.assertFalse(w.bar.get_sensitive())
            self.assertFalse(w.chat.get_sensitive())
            self.assertFalse(w.actions.get_sensitive())
            w.open_review()  # No concurrent checkout or installation.
            constructor.assert_called_once()
            release.set()
            self.wait_until(lambda: w.build is None)
        self.assertEqual(w.views.get_visible_child_name(), 'map')
        board = w.views.get_child_by_name('map')
        self.assertTrue(board.dependencies.loaded)
        self.assertEqual(board.dependencies.graph.edges, result['links']['edges'])
        self.assertEqual(board.dependencies.kinds, set())
        self.assertEqual(w.build_progress.bar.get_fraction(), 1)
        self.assertEqual(w.review_head, 'abc')
        self.assertTrue(w.chat.get_sensitive())
        self.assertTrue(w.bar.get_sensitive())
        board.destroy()

    def test_install_failure_stays_on_failed_step_without_a_map(self):
        w = self.window
        w.show_overview(('origin/feature', self.metadata))
        job, _ = self.build_fixture()
        def run(progress):
            progress(1, 'Running pnpm…')
            raise RuntimeError('pnpm failed')
        job.run.side_effect = run
        with patch('ReviewBuild.ReviewBuild', return_value=job):
            w.open_review()
            self.wait_until(lambda: w.build is None)
        self.assertEqual(w.views.get_visible_child_name(), 'loading')
        self.assertIn('Install dependencies', w.build_progress.title.get_text())
        self.assertEqual(w.build_progress.detail.get_text(), 'pnpm failed')
        self.assertIsNone(w.views.get_child_by_name('map'))
        self.assertTrue(w.actions.get_sensitive())

    def test_cancel_keeps_controls_locked_until_worker_stops_and_rejects_late_result(self):
        import threading
        w = self.window
        job, result = self.build_fixture()
        release = threading.Event()
        self.addCleanup(release.set)
        def run(progress):
            progress(1, 'Installing packages…')
            release.wait(2)
            return result
        job.run.side_effect = run
        with patch('ReviewBuild.ReviewBuild', return_value=job):
            w.open_review()
            self.wait_until(lambda: w.build_progress.stage == 1)
            w.cancel_review_build()
            job.cancel.assert_called_once()
            self.assertFalse(w.actions.get_sensitive())
            self.assertIs(w.build, job)
            release.set()
            self.wait_until(lambda: w.build is None)
        self.assertIsNone(w.views.get_child_by_name('map'))
        self.assertIsNone(w.review_head)
        self.assertIn('cancelled', w.build_progress.detail.get_text())
        self.assertTrue(w.actions.get_sensitive())

    def test_link_failure_still_opens_diff_with_a_visible_notice(self):
        w = self.window
        job, result = self.build_fixture()
        result.update(links=None, links_error='Compiler failed')
        job.run.return_value = result
        with patch('ReviewBuild.ReviewBuild', return_value=job):
            w.open_review()
            self.wait_until(lambda: w.build is None)
        self.assertEqual(w.views.get_visible_child_name(), 'map')
        board = w.views.get_child_by_name('map')
        self.assertIn('Compiler failed', board.dependencies.status.get_text())
        self.assertFalse(board.dependencies.loaded)
        board.destroy()

    def test_recent_reviews_show_reopened_item_first_on_dashboard(self):
        w = self.window
        w.remember_selection('pr', '12', 'First review')
        w.remember_selection('branch', 'feature', 'Second review')
        w.remember_selection('pr', '12', 'First review')
        self.assertEqual([item['value'] for item in w.recent_selections()], ['12', 'feature'])
        w.show_overview(('origin/feature', self.metadata))
        w.reset_selection()
        self.assertIs(w.recent_panel.get_parent(), w.canvas)
        buttons = [child for child in w.recent_panel.get_children() if isinstance(child, Gtk.Button)]
        self.assertEqual([button.get_label() for button in buttons],
                         ['Pr · Example change', 'Branch · Second review'])

    def setUp(self):
        import tempfile
        self.state_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.state_directory.cleanup)
        self.environment = patch.dict('os.environ', {'XDG_STATE_HOME': self.state_directory.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
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

    def test_flow_uses_current_python(self):
        import sys
        with patch('review_app.subprocess.Popen') as launch:
            self.window.flow(None)
        self.assertEqual(launch.call_args.args[0][0], sys.executable)

    def labels(self):
        return [w.get_text() for w in self.window.canvas.get_children()[0].get_children()
                if isinstance(w, Gtk.Label)]

    def test_branch_search_is_fuzzy_and_prefers_literal_matches(self):
        w = self.window
        w.branch_options = ['feat/einvoice-error-checklist-model', 'feat/einvcheck', 'other']
        w.branch.set_text('EINV CHECK')
        labels = [row.get_child().get_text() for row in w.branch_list.get_children()]
        self.assertEqual(labels, ['feat/einvcheck', 'feat/einvoice-error-checklist-model'])
        w.branch.set_text('')
        self.assertEqual([row.get_child().get_text() for row in w.branch_list.get_children()], w.branch_options)
        w.branch.set_text('zzzz')
        self.assertFalse(w.branch_list.get_row_at_index(0).get_activatable())

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
        self.assertEqual(attached['selection_diff_rows'], [1, 2])
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
