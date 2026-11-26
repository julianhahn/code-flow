"""Shortcut and feature overview. Opens on first start and from the ? button or the ? key."""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from ui import button, label, style

# The single list of user-facing keys and features. Update it when a shortcut changes.
HELP = [
    ('Move around the diff map', [
        ('Click a card', 'Select it. Scrolling never changes the selection'),
        ('↑ ↓ ← →', 'Select the nearest file card in that direction and center it'),
        ('Shift + ↑ ↓ ← →', 'Scroll the map in that direction'),
        ('V', 'Mark the selected file as viewed'),
        ('Ctrl + F', 'Search inside the diffs'),
    ]),
    ('Zoom and pan', [
        ('Ctrl + scroll, + / −', 'Zoom in and out'),
        ('Two-finger scroll, middle-drag', 'Pan the map'),
        ('Double-click a card', 'Zoom in and center that card'),
        ('Fit all', 'Zoom out to see every card and what is still unviewed'),
    ]),
    ('Reduce noise', [
        ('Hide specs', 'Remove test and spec files from the map'),
        ('Reference mode', 'Show which changed files the selected file uses, and which use it'),
    ]),
    ('Anywhere', [
        ('Ctrl + B, Sidebar', 'Hide or show the sidebar (chat, files, notes) for more room'),
        ('?', 'Show this help again'),
    ]),
]


def build_help_window(parent=None):
    window = Gtk.Window(title='Code Flow — Shortcuts', transient_for=parent)
    window.set_default_size(620, -1)
    window.set_modal(False)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=24)
    window.add(box)
    box.pack_start(label('KEYS AND FEATURES', 'eyebrow'), False, False, 0)
    for title, rows in HELP:
        box.pack_start(label(title.upper(), 'section'), False, False, 0)
        grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        for index, (keys, text) in enumerate(rows):
            keycap = label(keys, 'keycap')
            keycap.set_valign(Gtk.Align.CENTER)
            keycap.set_halign(Gtk.Align.START)
            grid.attach(keycap, 0, index, 1, 1)
            grid.attach(label(text, wrap=True), 1, index, 1, 1)
        box.pack_start(grid, False, False, 0)
    close = button('Got it', primary=True, on_click=lambda *_: window.destroy())
    close.set_halign(Gtk.Align.END)
    close.set_margin_top(12)
    box.pack_start(close, False, False, 0)
    window.show_all()
    return window
