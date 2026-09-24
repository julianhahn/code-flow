"""Diff-map rendering regressions."""
import unittest
from unittest.mock import Mock, patch

class RenderingTests(unittest.TestCase):
    def test_review_build_has_no_dependency_when_loading_branch_diff(self):
        from ReviewBuild import ReviewBuild
        build = ReviewBuild('/tmp/review-clone', 'feature/test', 'main')
        self.assertFalse(hasattr(build, 'dependency'))

    def pump_until(self, condition):
        import time
        from gi.repository import GLib
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
        self.assertTrue(condition())

    def test_gh_runner_reports_errors_without_dependency_package(self):
        from ReviewBuild import ReviewBuild
        build = ReviewBuild('/tmp/review-clone', 'feature/test', 'main')
        with patch('subprocess.run', return_value=Mock(returncode=0, stdout='{}', stderr='')) as run:
            messages = []
            result = build.run_gh(['gh', 'pr', 'view', '1'], messages.append)
        self.assertEqual(result, '{}')
        self.assertEqual(messages, ['Checking GitHub…'])
        self.assertFalse(hasattr(build, 'dependency'))
        self.assertEqual(run.call_args.kwargs['timeout'], 45)

    def test_incremental_cards_keep_order_and_report_each_file(self):
        from diff_canvas import DiffCanvas
        files = [('M', path, path, '@@ -1 +1 @@\n-old\n+new') for path in
                 ('pkgs/demo-backend/src/c.ts', 'pkgs/demo-shared/src/b.ts', 'pkgs/demo-frontend/src/a.ts')]
        sync = DiffCanvas(files)
        async_canvas = DiffCanvas(files, defer_render=True)
        self.addCleanup(sync.destroy)
        self.addCleanup(async_canvas.destroy)
        self.assertEqual(async_canvas.cards, {})
        events, done, errors = [], [], []
        async_canvas.render_incrementally(lambda *args: events.append(args), lambda: done.append(True), errors.append)
        self.assertIsNotNone(async_canvas.render_source)
        self.pump_until(lambda: bool(done or errors))
        self.assertEqual(errors, [])
        self.assertEqual([event[0] for event in events], [1, 2, 3])
        self.assertEqual([event[1] for event in events], [3, 3, 3])
        self.assertEqual(async_canvas.card_positions, sync.card_positions)
        self.assertIsNone(async_canvas.render_source)

    def test_render_cancel_and_failure_do_not_publish_a_partial_map(self):
        from diff_canvas import DiffCanvas
        canvas = DiffCanvas([('M', 'a.ts', 'a.ts', '')], defer_render=True)
        self.addCleanup(canvas.destroy)
        done, failed = Mock(), Mock()
        canvas.render_incrementally(Mock(), done, failed)
        canvas.cancel_render()
        self.assertIsNone(canvas.render_source)
        self.assertIsNone(canvas.render_iterator)
        done.assert_not_called()
        with patch.object(canvas, 'card', side_effect=RuntimeError('Draw failed')):
            canvas.render_incrementally(Mock(), done, failed)
            self.pump_until(lambda: failed.called)
        failed.assert_called_once_with('Draw failed')
        done.assert_not_called()
        self.assertFalse(canvas.rendering)

    def test_progress_lists_stages_as_labels_not_buttons(self):
        from ReviewBuildProgress import ReviewBuildProgress, Gtk
        cancel, back = Mock(), Mock()
        widget = ReviewBuildProgress(cancel, back)
        self.addCleanup(widget.destroy)
        widget.start()
        widget.update(1, 'Installing packages…')
        self.assertTrue(all(isinstance(step, Gtk.Label) for step in widget.steps))
        self.assertEqual([step.get_text() for step in widget.steps], [
            '✓ 1. Checkout', '→ 2. Read changes', '○ 3. Draw map'])
        self.assertAlmostEqual(widget.bar.get_fraction(), 1 / 3)
        self.assertEqual(widget.bar.get_text(), 'Step 2 of 3 · Read changes')
        widget.button.clicked()
        cancel.assert_called_once()
        widget.failed('Read failed')
        self.assertIn('Read changes', widget.title.get_text())
        self.assertFalse(widget.spinner.get_property('active'))
        widget.button.clicked()
        back.assert_called_once()
        widget.start()
        widget.update(2, '1 / 2 file cards', .5)
        self.assertAlmostEqual(widget.bar.get_fraction(), 5 / 6)
        widget.finish()
        self.assertEqual(widget.bar.get_fraction(), 1)
        self.assertFalse(widget.active)


if __name__ == '__main__':
    unittest.main()
