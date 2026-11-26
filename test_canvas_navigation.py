"""Keep normal scrolling in GTK, not a platform-specific canvas handler."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from diff_canvas import DiffCanvas, Gdk


class NavigationTests(unittest.TestCase):
    def test_plain_scroll_is_left_to_gtk(self):
        canvas = SimpleNamespace(queue_zoom=Mock())
        for direction in (Gdk.ScrollDirection.SMOOTH, Gdk.ScrollDirection.UP,
                          Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.LEFT,
                          Gdk.ScrollDirection.RIGHT):
            event = SimpleNamespace(state=0, direction=direction)
            self.assertFalse(DiffCanvas.wheel(canvas, None, event))
        canvas.queue_zoom.assert_not_called()

    def test_control_scroll_zooms(self):
        for direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.SMOOTH):
            canvas = SimpleNamespace(zoom=1.0, pending_zoom=None, queue_zoom=Mock())
            event = SimpleNamespace(state=Gdk.ModifierType.CONTROL_MASK,
                                    direction=direction,
                                    get_scroll_deltas=lambda: (True, 0, -1))
            self.assertTrue(DiffCanvas.wheel(canvas, None, event))
            canvas.queue_zoom.assert_called_once()
            self.assertGreater(canvas.queue_zoom.call_args.args[0], 1)

    def test_fit_all_shows_whole_map_and_double_click_centers_card(self):
        from gi.repository import Gtk
        files = [('M', f'src/f{i}.py', f'src/f{i}.py', '@@ -1 +1 @@\n-old\n+new') for i in range(6)]
        canvas = DiffCanvas(files)
        try:
            window = Gtk.OffscreenWindow()
            window.set_default_size(800, 600)
            window.add(canvas)
            window.show_all()
            def pump():
                for _ in range(50):
                    while Gtk.events_pending():
                        Gtk.main_iteration()
            pump()
            canvas.fit_all()
            pump()
            self.assertLess(canvas.zoom, 1.0)
            self.assertEqual(canvas.scroll.get_hadjustment().get_value(), 0)
            path = files[3][2]
            canvas.center_on_file(path)
            pump()
            self.assertGreaterEqual(canvas.zoom, 1.0)
            card = next(p for p in canvas.card_positions if p[0][2] == path)
            vertical = canvas.scroll.get_vadjustment()
            expected = card[2] + card[4] / 2 - vertical.get_page_size() / 2
            self.assertAlmostEqual(vertical.get_value(), max(0, expected), delta=2)
        finally:
            canvas.destroy()

    def test_first_populated_column_starts_at_left_edge(self):
        canvas = DiffCanvas([('M', 'a.py', 'a.py', '@@ -1 +1 @@\n-old\n+new')])
        try:
            self.assertEqual(canvas.card_positions[0][1], 40)
        finally:
            canvas.destroy()
