import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
