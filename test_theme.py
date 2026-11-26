import re
import unittest
from pathlib import Path

import theme
from ui import badge, label, style

ROOT = Path(__file__).parent
SOURCES = [p for p in ROOT.glob('*.py') if p.name not in ('theme.py', 'code-flow.py') and not p.name.startswith('test_')]


class ThemeTests(unittest.TestCase):
    def test_every_text_on_background_pair_is_readable(self):
        for fg, bg in theme.PAIRS:
            self.assertGreaterEqual(theme.contrast(fg, bg), 4.5, f'{fg} on {bg}')

    def test_css_parses(self):
        from gi.repository import Gtk
        Gtk.CssProvider().load_from_data(theme.CSS.encode())

    def test_no_color_literals_outside_theme(self):
        # code-flow.py (flow viewer) draws with cairo floats and is not themed yet.
        for path in SOURCES:
            self.assertEqual(re.findall(r'#[0-9a-fA-F]{6}\b', path.read_text()), [], path.name)

    def test_no_direct_style_classes_or_css_providers(self):
        for path in SOURCES:
            text = path.read_text()
            if path.name != 'ui.py':
                self.assertNotIn('.add_class(', text.replace("remove_class", ""), path.name)
            if path.name != 'diff_canvas.py':
                self.assertNotIn('CssProvider(', text, path.name)

    def test_unknown_names_fail_loudly(self):
        from gi.repository import Gtk
        with self.assertRaises(ValueError):
            style(Gtk.Label(), 'no-such-class')
        with self.assertRaises(ValueError):
            badge('x', 'purple')
        with self.assertRaises(ValueError):
            label('x', 'huge')


if __name__ == '__main__':
    unittest.main()
