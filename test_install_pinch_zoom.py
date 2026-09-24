import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from install_pinch_zoom import install_pinch_zoom


class PinchTests(unittest.TestCase):
    def test_gtk_pinch_uses_start_scale(self):
        from gi.repository import Gtk
        canvas = Mock(zoom=1.0, pending_zoom=None)
        with patch('install_pinch_zoom.sys.platform', 'linux'), patch.object(Gtk.GestureZoom, 'new') as new:
            gesture = install_pinch_zoom(canvas)
        handlers = {call.args[0]: call.args[1] for call in gesture.connect.call_args_list}
        handlers['begin']()
        handlers['scale-changed'](gesture, .8)
        canvas.pending_zoom = .8
        handlers['scale-changed'](gesture, .5)
        self.assertEqual(canvas.queue_zoom.call_args.args[0], .5)

    def native(self):
        canvas = Mock(zoom=1.0, pending_zoom=None)
        canvas.get_mapped.return_value = True
        canvas.viewport.get_pointer.return_value = (20, 30)
        canvas.viewport.get_allocated_width.return_value = 500
        canvas.viewport.get_allocated_height.return_value = 400
        objc, gdk = Mock(), Mock()
        objc.sel_registerName.side_effect = lambda name: name
        gdk.gdk_quartz_window_get_nswindow.return_value = 123
        event = {'type': 30, 'window': 123, 'magnification': -.2}
        message = lambda native, selector: event[selector.decode()]
        factories = [lambda address: message] * 3 + [lambda callback: callback]
        with patch('install_pinch_zoom.sys.platform', 'darwin'), \
                patch('install_pinch_zoom.ctypes.util.find_library', side_effect=lambda name: name), \
                patch('install_pinch_zoom.ctypes.CDLL', side_effect=[objc, gdk]), \
                patch('install_pinch_zoom.ctypes.cast', return_value=SimpleNamespace(value=1)), \
                patch('install_pinch_zoom.ctypes.CFUNCTYPE', side_effect=factories):
            callback = install_pinch_zoom(canvas)
        return canvas, gdk, event, callback

    def test_mac_pinch_accumulates_pending_zoom_and_unregisters(self):
        canvas, gdk, event, callback = self.native()
        self.assertEqual(callback(1, None, None), 2)
        canvas.queue_zoom.assert_called_with(.8)
        canvas.pending_zoom = .8
        callback(1, None, None)
        self.assertAlmostEqual(canvas.queue_zoom.call_args.args[0], .64)
        canvas.connect.call_args.args[1]()
        gdk.gdk_window_remove_filter.assert_called_once_with(None, callback, None)

    def test_mac_scroll_other_windows_and_outside_canvas_are_untouched(self):
        for changes, position in (({'type': 22}, (20, 30)),
                                  ({'window': 456}, (20, 30)),
                                  ({}, (-1, 30))):
            canvas, _, event, callback = self.native()
            event.update(changes)
            canvas.viewport.get_pointer.return_value = position
            self.assertEqual(callback(1, None, None), 0)
            canvas.queue_zoom.assert_not_called()
