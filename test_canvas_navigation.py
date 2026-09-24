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

    def test_first_populated_column_starts_at_left_edge(self):
        canvas = DiffCanvas([('M', 'a.py', 'a.py', '@@ -1 +1 @@\n-old\n+new')])
        try:
            self.assertEqual(canvas.card_positions[0][1], 40)
        finally:
            canvas.destroy()
