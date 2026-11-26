"""Keep normal scrolling in GTK, not a platform-specific canvas handler."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from diff_canvas import DiffCanvas, Gdk, is_spec


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

    def test_is_spec_matches_common_test_files(self):
        for path in ('a/b.spec.ts', 'a/b.test.tsx', 'a/b_spec.rb', 'a/b_test.go', 'a/test_b.py',
                     'a/__tests__/b.ts', 'spec/b.rb', 'a/__snapshots__/b.snap'):
            self.assertTrue(is_spec(path), path)
        for path in ('a/b.ts', 'a/specification.ts', 'a/latest.py', 'a/contest/b.py'):
            self.assertFalse(is_spec(path), path)

    def test_hide_specs_toggle_removes_spec_cards(self):
        files = [('M', p, p, '@@ -1 +1 @@\n-old\n+new') for p in ('src/a.ts', 'src/a.spec.ts')]
        canvas = DiffCanvas(files)
        try:
            self.assertEqual(set(canvas.cards), {'src/a.ts', 'src/a.spec.ts'})
            canvas.spec_toggle.set_active(True)
            self.assertEqual(set(canvas.cards), {'src/a.ts'})
            self.assertIn('/ 1 viewed', canvas.read_count.get_text())
            canvas.spec_toggle.set_active(False)
            self.assertEqual(set(canvas.cards), {'src/a.ts', 'src/a.spec.ts'})
        finally:
            canvas.destroy()

    def test_arrow_navigation_picks_nearest_card_in_direction(self):
        def card(name, x, y):
            return ((('M', name, name, '')), x, y, 100, 50)
        positions = [card('a', 0, 0), card('b', 0, 100), card('c', 0, 200), card('d', 300, 100)]
        canvas = SimpleNamespace(card_positions=positions, active_file=positions[1][0])
        neighbor = lambda key: DiffCanvas.neighbor_path(canvas, key)
        self.assertEqual(neighbor('down'), 'c')
        self.assertEqual(neighbor('up'), 'a')
        self.assertEqual(neighbor('right'), 'd')
        self.assertIsNone(neighbor('left'))
        canvas.active_file = positions[2][0]
        self.assertIsNone(neighbor('down'))
        canvas.active_file = None
        self.assertEqual(neighbor('down'), 'a')

    def test_first_populated_column_starts_at_left_edge(self):
        canvas = DiffCanvas([('M', 'a.py', 'a.py', '@@ -1 +1 @@\n-old\n+new')])
        try:
            self.assertEqual(canvas.card_positions[0][1], 40)
        finally:
            canvas.destroy()
