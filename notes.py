"""Local PR notes with persistent selected-text formatting."""
import json
import os
import sqlite3
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango


class Notes(Gtk.Box):
    def __init__(self, database=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        if database is None:
            directory = Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state')) / 'code-flow'
            directory.mkdir(parents=True, exist_ok=True)
            database = directory / 'notes.sqlite3'
        self.database = sqlite3.connect(str(database))
        self.database.execute('CREATE TABLE IF NOT EXISTS notes (pr INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        if 'formatting' not in {row[1] for row in self.database.execute('PRAGMA table_info(notes)')}:
            self.database.execute("ALTER TABLE notes ADD COLUMN formatting TEXT NOT NULL DEFAULT '[]'")
        self.database.commit()
        self.number = None
        self.dead = False
        self.loading = False
        self.save_source = None
        self.dirty = False
        self.pack_start(Gtk.Label(label='Review notes', xalign=0), False, False, 0)
        self.toolbar = Gtk.Box(spacing=6)
        for name, label, key in [('bold', 'Bold', 'B'), ('italic', 'Italic', 'I'), ('underline', 'Underline', 'U')]:
            button = Gtk.Button(label=label)
            button.set_tooltip_text('Select text · Ctrl+' + key)
            button.set_focus_on_click(False)
            button.connect('clicked', lambda _, style=name: self.format_selection(style))
            self.toolbar.pack_start(button, False, False, 0)
        self.pack_start(self.toolbar, False, False, 0)
        self.message = Gtk.Label(label='Select a PR to write notes.', xalign=0)
        self.message.set_line_wrap(True)
        self.message.get_style_context().add_class('muted')
        self.editor = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        for setter in (self.editor.set_left_margin, self.editor.set_right_margin,
                       self.editor.set_top_margin, self.editor.set_bottom_margin):
            setter(16)
        self.editor.set_pixels_below_lines(5)
        self.editor.set_editable(False)
        self.toolbar.set_sensitive(False)
        buffer = self.editor.get_buffer()
        self.tags = {
            'bold': buffer.create_tag('bold', weight=Pango.Weight.BOLD),
            'italic': buffer.create_tag('italic', style=Pango.Style.ITALIC),
            'underline': buffer.create_tag('underline', underline=Pango.Underline.SINGLE),
        }
        buffer.connect('changed', self.changed)
        self.editor.connect('key-press-event', self.key_press)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.editor)
        self.pack_start(scroll, True, True, 0)
        self.pack_start(self.message, False, False, 0)
        self.connect('destroy', self.close)

    def key_press(self, _, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        if event.state & (Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        style = {'b': 'bold', 'i': 'italic', 'u': 'underline'}.get(chr(event.keyval).lower())
        if style:
            self.format_selection(style)
            return True
        return False

    def format_selection(self, style):
        if self.number is None:
            return
        buffer = self.editor.get_buffer()
        bounds = buffer.get_selection_bounds()
        if not bounds:
            self.message.set_text('Select text to format it.')
            return
        start, end = bounds
        tag = self.tags[style]
        all_tagged = all(buffer.get_iter_at_offset(i).has_tag(tag)
                         for i in range(start.get_offset(), end.get_offset()))
        (buffer.remove_tag if all_tagged else buffer.apply_tag)(tag, start, end)
        self.changed()

    def configure(self, number):
        if number == self.number:
            return
        if not self.flush():
            raise RuntimeError('Could not save notes. Stay on this PR and retry.')
        row = self.database.execute('SELECT body, formatting FROM notes WHERE pr=?', (number,)).fetchone() if number is not None else None
        self.loading = True
        buffer = self.editor.get_buffer()
        buffer.set_text(row[0] if row else '')
        if row:
            for name, start, end in json.loads(row[1]):
                if name in self.tags and 0 <= start < end <= buffer.get_char_count():
                    buffer.apply_tag(self.tags[name], buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end))
        self.loading = False
        self.number = number
        self.editor.set_editable(number is not None)
        self.toolbar.set_sensitive(number is not None)
        self.message.set_text('Saved locally · Kept across commits' if number else 'Select a PR to write notes.')

    def changed(self, *_):
        if self.loading or self.number is None or self.dead:
            return
        self.dirty = True
        self.message.set_text('Saving…')
        if self.save_source is None:
            self.save_source = GLib.timeout_add(300, self.save)

    def flush(self):
        if self.save_source is not None:
            GLib.source_remove(self.save_source)
            self.save_source = None
        if self.dirty:
            self.save()
        return not self.dirty

    def save(self):
        self.save_source = None
        if not self.dirty:
            return False
        buffer = self.editor.get_buffer()
        body = buffer.get_text(*buffer.get_bounds(), True)
        spans = []
        for name, tag in self.tags.items():
            start = None
            for offset in range(buffer.get_char_count() + 1):
                active = offset < buffer.get_char_count() and buffer.get_iter_at_offset(offset).has_tag(tag)
                if active and start is None:
                    start = offset
                elif not active and start is not None:
                    spans.append([name, start, offset])
                    start = None
        try:
            with self.database:
                self.database.execute('INSERT OR REPLACE INTO notes (pr, body, formatting) VALUES (?, ?, ?)',
                                      (self.number, body, json.dumps(spans)))
        except sqlite3.Error as error:
            self.message.set_text('Could not save notes: ' + str(error))
            return False
        self.dirty = False
        self.message.set_text('Saved locally · Kept across commits')
        return False

    def close(self, *_):
        if self.dead:
            return
        self.flush()
        self.dead = True
        self.database.close()
