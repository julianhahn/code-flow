"""Card controls must shrink with the map, even with the app stylesheet."""
import time
import unittest
from pathlib import Path

from diff_canvas import DiffCanvas, Gtk


class CardControlTests(unittest.TestCase):
    def test_selected_card_bounds_match_layout_after_text_validation(self):
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        patch = '@@ -0,0 +17 @@\n' + '\n'.join('+' + 'hello world ' * 22 for _ in range(17))
        canvas = DiffCanvas([('A', name, name, patch) for name in ('a.py', 'b.py')])
        window.add(canvas)
        window.show_all()
        canvas.set_zoom(0.5)
        canvas.cards['a.py'].get_style_context().add_class('focused-card')
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            if Gtk.events_pending():
                Gtk.main_iteration()
        for file, x, y, width, height in canvas.card_positions:
            card = canvas.cards[file[2]]
            self.assertEqual(card.get_allocated_height(), height)
        first = canvas.cards['a.py'].get_allocation()
        second = canvas.cards['b.py'].get_allocation()
        self.assertLess(first.y + first.height, second.y)

    def test_outline_scales_and_code_has_no_blank_bottom_strip(self):
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        patch = '@@ -0,0 +17 @@\n' + '\n'.join('+' + 'hello world ' * 22 for _ in range(17))
        canvas = DiffCanvas([('A', 'a.py', 'a.py', patch)])
        window.add(canvas)
        window.show_all()
        for zoom in (1.0, 0.5, 0.25):
            with self.subTest(zoom=zoom):
                canvas.set_zoom(zoom)
                card = canvas.cards['a.py']
                card.get_style_context().add_class('focused-card')
                deadline = time.monotonic() + 0.2
                while time.monotonic() < deadline:
                    if Gtk.events_pending():
                        Gtk.main_iteration()
                border = card.get_style_context().get_border(Gtk.StateFlags.NORMAL)
                self.assertEqual(border.bottom, max(1, round(3 * zoom)))
                text = card.get_children()[1]
                end = text.get_iter_location(text.get_buffer().get_end_iter())
                self.assertAlmostEqual(text.get_allocated_height(), end.y + end.height, delta=1)

    def test_controls_shrink_and_copy_background_is_transparent(self):
        window = Gtk.Window()
        window.set_name('code-flow-review')
        self.addCleanup(window.destroy)
        canvas = DiffCanvas([('A', 'a.py', 'a.py', '@@ -0,0 +1 @@\n+hello')])
        window.add(canvas)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).with_name('review.css')))
        screen = window.get_screen()
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.addCleanup(Gtk.StyleContext.remove_provider_for_screen, screen, provider)
        window.show_all()
        sizes = []
        for zoom in (1.0, 0.25):
            canvas.set_zoom(zoom)
            header = canvas.cards['a.py'].get_children()[0]
            viewed, copy = header.get_children()[-2:]
            sizes.append((viewed.get_preferred_height()[1], copy.get_preferred_height()[1]))
            background = copy.get_style_context().get_background_color(Gtk.StateFlags.NORMAL)
            self.assertEqual(background.alpha, 0)
            self.assertEqual(copy.get_label(), '⧉')
        self.assertLess(sizes[1][0], sizes[0][0])
        self.assertLess(sizes[1][1], sizes[0][1])
        self.assertLess(sizes[1][1], 25)


if __name__ == '__main__':
    unittest.main()
