import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import review_app


class SessionLoggingTests(unittest.TestCase):
    def test_log_file_is_unique_and_captures_process_output(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(review_app.tempfile, 'gettempdir', return_value=temporary):
            original_stdout, original_stderr = review_app.sys.stdout, review_app.sys.stderr
            try:
                first = review_app.start_session_log()
                print('session marker: checkout started')
                review_app.sys.stdout.flush()
                second = review_app.start_session_log()
                print('session marker: second launch')
                review_app.sys.stdout.flush()
                self.assertNotEqual(first, second)
            finally:
                review_app.sys.stdout.close()
                review_app.sys.stdout = original_stdout
                review_app.sys.stderr = original_stderr
            self.assertIn('session marker: checkout started', first.read_text())
            self.assertIn('session marker: second launch', second.read_text())
            self.assertEqual(first.parent.name, 'code-flow-logs')

    def test_build_failure_logs_traceback_and_still_reports_to_window(self):
        build = Mock()
        build.run.side_effect = RuntimeError('checkout timed out')
        window = SimpleNamespace(
            build=None, destroyed=False, overview_branch='origin/feature',
            overview_pr=None, base=Mock(), views=Mock(), chat=Mock(),
            chat_busy=Mock(), canvas=Mock(), build_progress=Mock(),
            status=Mock(), build_failed=Mock(), show_review=Mock(),
            review_head=None,
        )
        window.base.get_text.return_value = 'origin/main'
        window.views.get_child_by_name.return_value = None
        window.chat.busy = False
        errors = io.StringIO()

        with patch('ReviewBuild.ReviewBuild', return_value=build), \
                patch.object(review_app.sys, 'stderr', errors), \
                patch.object(review_app.GLib, 'idle_add', side_effect=lambda callback, *args: callback(*args)), \
                patch.object(review_app.threading, 'Thread',
                             side_effect=lambda target, daemon: SimpleNamespace(start=target)):
            review_app.ReviewWindow.open_review(window)

        self.assertIn('Code Flow error: Diff-map build failed', errors.getvalue())
        self.assertIn('RuntimeError: checkout timed out', errors.getvalue())
        self.assertIn('Traceback (most recent call last):', errors.getvalue())
        window.build_failed.assert_called_once_with(build, 'checkout timed out')

    def test_unhandled_error_logs_traceback_and_opens_window_dialog(self):
        errors = io.StringIO()
        original_hook = review_app.sys.excepthook
        original_thread_hook = review_app.threading.excepthook
        try:
            with patch.object(review_app.sys, 'stderr', errors), \
                    patch.object(review_app.GLib, 'idle_add') as idle:
                review_app.install_exception_logging()
                try:
                    raise RuntimeError('unexpected callback failure')
                except RuntimeError as error:
                    review_app.sys.excepthook(type(error), error, error.__traceback__)
                try:
                    raise RuntimeError('unexpected thread failure')
                except RuntimeError as error:
                    review_app.threading.excepthook(SimpleNamespace(
                        exc_type=type(error), exc_value=error,
                        exc_traceback=error.__traceback__))
            self.assertIn('RuntimeError: unexpected callback failure', errors.getvalue())
            self.assertIn('RuntimeError: unexpected thread failure', errors.getvalue())
            self.assertEqual(idle.call_args_list, [
                unittest.mock.call(review_app.show_error_dialog,
                                   'unexpected callback failure'),
                unittest.mock.call(review_app.show_error_dialog,
                                   'unexpected thread failure'),
            ])
        finally:
            review_app.sys.excepthook = original_hook
            review_app.threading.excepthook = original_thread_hook

    def test_error_dialog_shows_message_in_review_window(self):
        window = Mock(spec=review_app.ReviewWindow)
        window.status = Mock()
        window.get_visible.return_value = True
        dialog = Mock()
        with patch.object(review_app.Gtk.Window, 'list_toplevels', return_value=[window]), \
                patch.object(review_app.Gtk, 'MessageDialog', return_value=dialog):
            self.assertFalse(review_app.show_error_dialog('unexpected callback failure'))
        window.status.set_text.assert_called_once_with(
            'Code Flow error: unexpected callback failure')
        dialog.format_secondary_text.assert_called_once_with('unexpected callback failure')
        dialog.run.assert_called_once_with()
        dialog.destroy.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
