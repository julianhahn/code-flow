"""Visual regression test for arrow-key navigation.

Renders a real DiffCanvas, presses Down/Up through cards of very different sizes and,
from the actual screen pixels, checks that the blue selection frame sits on the card that
should be selected. Pixels are read from the window itself, so no golden images are needed
and it works the same on macOS and Linux (xvfb).
"""
import time
import unittest

from diff_canvas import DiffCanvas, Gdk, Gtk
import theme
from theme import T

SMALL = '@@ -1 +1 @@\n-old\n+new'
# Wide and not very tall, like eInvoiceConnectionAtoms.spec.ts (856x517) next to 300x27 cards.
BIG = '@@ -1,20 +1,20 @@\n' + '\n'.join(f'-{"x" * 140} old {i}\n+{"y" * 140} new {i}' for i in range(10))
# Same shape as a real review: small cards, one very wide and tall card, small cards below it.
CARDS = [('src/a/A.ts', SMALL), ('src/a/B.ts', SMALL), ('src/a/deeper/C.spec.ts', BIG),
         ('src/a/deeper/D.ts', SMALL), ('src/a/deeper/E.ts', SMALL)]
ORDER = [path for path, _ in CARDS]


def pump(seconds=0.12):
    """Run the GTK main loop for a moment so layout, scrolling and painting are finished."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.01)


def frame_box(window, region):
    """Bounding box (left, top, right, bottom) of selection-frame pixels inside region, or None."""
    gw = window.get_window()
    width, height = window.get_allocated_width(), window.get_allocated_height()
    pixbuf = Gdk.pixbuf_get_from_window(gw, 0, 0, width, height)
    target = [int(T['accent'][i:i+2], 16) for i in (1, 3, 5)]
    channels, stride, data = pixbuf.get_n_channels(), pixbuf.get_rowstride(), pixbuf.get_pixels()
    scale = pixbuf.get_width() / width
    left, top, right, bottom = [round(v * scale) for v in region]
    xs, ys = [], []
    for y in range(top, min(bottom, pixbuf.get_height())):
        row = y * stride
        for x in range(left, min(right, pixbuf.get_width())):
            pixel = data[row + x * channels: row + x * channels + 3]
            if all(abs(pixel[i] - target[i]) <= 6 for i in range(3)):
                xs.append(x); ys.append(y)
    if not xs:
        return None
    return tuple(round(v / scale) for v in (min(xs), min(ys), max(xs), max(ys)))


class NavigationVisualTests(unittest.TestCase):
    def setUp(self):
        files = [('M', path, path, patch) for path, patch in CARDS]
        self.canvas = DiffCanvas(files)
        self.window = Gtk.Window()
        self.window.set_default_size(1000, 700)
        provider = theme.apply(self.window.get_screen())
        self.addCleanup(Gtk.StyleContext.remove_provider_for_screen, self.window.get_screen(), provider)
        self.window.add(self.canvas)
        self.window.show_all()
        pump()
        self.addCleanup(self.window.destroy)

    def viewport(self):
        scroll = self.canvas.scroll
        x, y = scroll.translate_coordinates(self.window, 0, 0)
        return (x, y, x + scroll.get_allocated_width(), y + scroll.get_allocated_height())

    def assert_frame_on(self, path, step):
        pump()
        card = self.canvas.cards[path]
        card_x, card_y = card.translate_coordinates(self.window, 0, 0)
        box = frame_box(self.window, self.viewport())
        message = f'{step}: expected the selection frame on {path}, active={self.canvas.active_file[2]}, box={box}'
        self.assertEqual(self.canvas.active_file[2], path, message)
        self.assertIsNotNone(box, message)
        self.assertAlmostEqual(box[0], card_x, delta=4, msg=message)
        view_top = self.viewport()[1]
        if card_y >= view_top:  # a card taller than the window may start above the view
            self.assertAlmostEqual(box[1], card_y, delta=4, msg=message)
        self.assertGreater(box[3] - box[1], 10, message)

    def test_down_visits_every_card_in_order(self):
        self.canvas.set_active(ORDER[0])
        self.assert_frame_on(ORDER[0], 'start')
        for index, path in enumerate(ORDER[1:], start=1):
            self.canvas.navigate('down')
            self.assert_frame_on(path, f'down #{index}')

    def test_up_visits_every_card_in_reverse_order(self):
        self.canvas.set_active(ORDER[-1])
        self.canvas.center_on_file(ORDER[-1], 0)
        for index, path in enumerate(reversed(ORDER[:-1]), start=1):
            self.canvas.navigate('up')
            self.assert_frame_on(path, f'up #{index}')

    def test_selected_card_is_fully_on_screen_when_it_fits(self):
        self.canvas.set_active(ORDER[1])
        self.canvas.navigate('down')
        pump()
        card = self.canvas.cards[ORDER[2]]
        _, card_y = card.translate_coordinates(self.window, 0, 0)
        view_top, view_bottom = self.viewport()[1], self.viewport()[3]
        self.assertGreaterEqual(card_y, view_top)
        self.assertLessEqual(card_y + card.get_allocated_height(), view_bottom)


if __name__ == '__main__':
    unittest.main()
