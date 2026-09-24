"""Pinch zoom without replacing GTK's normal scroll handling."""
import ctypes
import ctypes.util
import sys


def install_pinch_zoom(canvas):
    from gi.repository import Gtk

    if sys.platform != 'darwin':
        gesture = Gtk.GestureZoom.new(canvas.scroll)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        start = [1.0]

        def begin(*_):
            start[0] = canvas.pending_zoom if canvas.pending_zoom is not None else canvas.zoom

        gesture.connect('begin', begin)
        gesture.connect('scale-changed', lambda _, scale: canvas.queue_zoom(start[0] * scale))
        return gesture

    # GTK 3 Quartz does not expose NSEvent magnification as Gtk.GestureZoom.
    # Listen only for magnification; leave wheel/trackpad scrolling untouched.
    objc = ctypes.CDLL(ctypes.util.find_library('objc'))
    gdk = ctypes.CDLL(ctypes.util.find_library('gdk-3'))
    pointer = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = pointer
    selectors = {name: objc.sel_registerName(name.encode())
                 for name in ('type', 'window', 'magnification')}
    address = ctypes.cast(objc.objc_msgSend, pointer).value
    integer = ctypes.CFUNCTYPE(ctypes.c_ulong, pointer, pointer)(address)
    object_value = ctypes.CFUNCTYPE(pointer, pointer, pointer)(address)
    double = ctypes.CFUNCTYPE(ctypes.c_double, pointer, pointer)(address)
    gdk.gdk_quartz_window_get_nswindow.argtypes = [pointer]
    gdk.gdk_quartz_window_get_nswindow.restype = pointer
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, pointer, pointer, pointer)

    def observe(native, event, data):
        if integer(native, selectors['type']) != 30:  # NSEventTypeMagnify
            return 0
        window = canvas.get_toplevel().get_window()
        if window is None or not canvas.get_mapped():
            return 0
        # PyGObject's hash is the underlying GObject address.
        own_window = gdk.gdk_quartz_window_get_nswindow(hash(window))
        if object_value(native, selectors['window']) != own_window:
            return 0
        x, y = canvas.viewport.get_pointer()
        if not (0 <= x < canvas.viewport.get_allocated_width()
                and 0 <= y < canvas.viewport.get_allocated_height()):
            return 0
        target = canvas.pending_zoom if canvas.pending_zoom is not None else canvas.zoom
        canvas.queue_zoom(target * max(.01, 1 + double(native, selectors['magnification'])))
        return 2  # GDK_FILTER_REMOVE: this pinch has been handled.

    callback = callback_type(observe)
    for name in ('gdk_window_add_filter', 'gdk_window_remove_filter'):
        function = getattr(gdk, name)
        function.argtypes = [pointer, callback_type, pointer]
        function.restype = None
    gdk.gdk_window_add_filter(None, callback, None)

    def stop(*_):
        # The signal closure keeps the native callback alive until removal.
        gdk.gdk_window_remove_filter(None, callback, None)

    canvas.connect('destroy', stop)
    return callback
