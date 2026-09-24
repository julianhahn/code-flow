"""Automatic build order and progress, without installing real dependencies."""
import threading
import unittest
from unittest.mock import Mock, patch

from ReviewBuild import ReviewBuild
from test_dependency_analysis import RepositoryFixture


class BuildTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.base = self.head
        self.write('src/a.ts', 'export function greet() { return 2; }\n')
        self.commit()
        self.git('checkout', '--detach', self.base)

    def build(self):
        return ReviewBuild(self.root, self.head, self.base)

    def test_checkout_install_read_analyse_order_and_real_progress(self):
        build = self.build()
        events = []
        def install(progress):
            self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)
            self.assertEqual(events[-1][0], 1)
            progress('Packages resolved: 12')
        def analyse(progress):
            self.assertEqual(events[-1][0], 3)
            progress('Analysing project 1/2')
            return {'edges': []}
        with patch.object(build.dependency, 'install', side_effect=install) as install_call, \
                patch.object(build.dependency, 'analyse', side_effect=analyse) as analyse_call:
            result = build.run(lambda *event: events.append(event))
        install_call.assert_called_once()
        analyse_call.assert_called_once()
        self.assertEqual(list(dict.fromkeys(event[0] for event in events)), [0, 1, 2, 3, 4])
        self.assertIn((1, 'Packages resolved: 12', 0), events)
        self.assertIn((2, '1 / 1 files', 1), events)
        self.assertEqual(result['head'], self.head)
        self.assertEqual(result['files'][0][2], 'src/a.ts')
        self.assertIn('+export function greet()', result['files'][0][3])
        self.assertEqual(result['file_commits']['src/a.ts'], self.head)
        self.assertIsNone(result['links_error'])

    def test_install_and_analysis_run_on_every_build_even_with_a_cache(self):
        build = self.build()
        with patch.object(build.dependency, 'install') as install, \
                patch.object(build.dependency, 'analyse', return_value={'cacheHit': True}) as analyse:
            build.run(lambda *_: None)
            build.run(lambda *_: None)
        self.assertEqual(install.call_count, 2)
        self.assertEqual(analyse.call_count, 2)

    def test_install_failure_stops_before_map_and_analysis(self):
        build = self.build()
        events = []
        with patch.object(build.dependency, 'install', side_effect=RuntimeError('pnpm failed')), \
                patch.object(build.dependency, 'analyse') as analyse:
            with self.assertRaisesRegex(RuntimeError, 'pnpm failed'):
                build.run(lambda *event: events.append(event))
        analyse.assert_not_called()
        self.assertEqual(events[-1][0], 1)

    def test_analysis_failure_preserves_diff_but_reports_missing_links(self):
        build = self.build()
        with patch.object(build.dependency, 'install'), \
                patch.object(build.dependency, 'analyse', side_effect=RuntimeError('Compiler failed')):
            result = build.run(lambda *_: None)
        self.assertIsNone(result['links'])
        self.assertEqual(result['links_error'], 'Compiler failed')
        self.assertEqual(len(result['files']), 1)

    def test_cancellation_during_install_never_starts_analysis(self):
        build = self.build()
        with patch.object(build.dependency, 'install', side_effect=lambda *_: build.cancel()), \
                patch.object(build.dependency, 'analyse') as analyse:
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                build.run(lambda *_: None)
        analyse.assert_not_called()

    def test_cancellation_during_analysis_is_not_treated_as_optional_failure(self):
        build = self.build()
        def analyse(progress):
            build.cancel()
            raise RuntimeError('Stopped')
        with patch.object(build.dependency, 'install'), patch.object(build.dependency, 'analyse', side_effect=analyse):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                build.run(lambda *_: None)

    def test_changed_checkout_after_analysis_blocks_the_map(self):
        build = self.build()
        def analyse(progress):
            self.git('checkout', '--detach', self.base)
            return {'edges': []}
        with patch.object(build.dependency, 'install'), patch.object(build.dependency, 'analyse', side_effect=analyse):
            with self.assertRaisesRegex(RuntimeError, 'checkout changed'):
                build.run(lambda *_: None)

    def test_dirty_checkout_blocks_automatic_install(self):
        self.write('src/a.ts', 'local edit')
        build = self.build()
        with patch.object(build.dependency, 'install') as install:
            with self.assertRaisesRegex(RuntimeError, 'local changes'):
                build.run(lambda *_: None)
        install.assert_not_called()


class RenderingTests(unittest.TestCase):
    def pump_until(self, condition):
        import time
        from gi.repository import GLib
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
        self.assertTrue(condition())

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
            '✓ 1. Checkout', '→ 2. Install dependencies', '○ 3. Read changes', '○ 4. Find links', '○ 5. Draw map'])
        self.assertEqual(widget.bar.get_fraction(), .2)
        self.assertEqual(widget.bar.get_text(), 'Step 2 of 5 · Install dependencies')
        widget.button.clicked()
        cancel.assert_called_once()
        widget.failed('pnpm failed')
        self.assertIn('Install dependencies', widget.title.get_text())
        self.assertFalse(widget.spinner.get_property('active'))
        widget.button.clicked()
        back.assert_called_once()
        widget.start()
        widget.update(4, '1 / 2 file cards', .5)
        self.assertEqual(widget.bar.get_fraction(), .9)
        widget.finish()
        self.assertEqual(widget.bar.get_fraction(), 1)
        self.assertFalse(widget.active)


if __name__ == '__main__':
    unittest.main()
