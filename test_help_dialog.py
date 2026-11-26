import unittest
from help_dialog import HELP, build_help_window


class HelpDialogTests(unittest.TestCase):
    def test_every_entry_has_keys_and_text_and_window_builds(self):
        for title, rows in HELP:
            self.assertTrue(title and rows)
            for keys, text in rows:
                self.assertTrue(keys.strip() and text.strip())
        window = build_help_window()
        self.addCleanup(window.destroy)
        self.assertTrue(window.get_visible())


if __name__ == '__main__':
    unittest.main()
