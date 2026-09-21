"""GTK chat panel; all Pi I/O stays on a worker thread."""
import threading
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Pango
from pi_chat import PiChat


class ChatPanel(Gtk.Box):
    def __init__(self, checkout, context, busy_changed):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
        self.set_size_request(390, -1)
        self.checkout = checkout
        self.context = context
        self.busy_changed = busy_changed
        self.client = None
        self.busy = False
        self.scope = None
        self.pack_start(Gtk.Label(label='Ask Pi · Read-only', xalign=0), False, False, 0)
        self.context_label = Gtk.Label(label='Select a PR, then open its diff map.', xalign=0)
        self.context_label.set_line_wrap(True)
        self.context_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.context_label.set_max_width_chars(42)
        self.pack_start(self.context_label, False, False, 0)
        self.messages = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.messages.set_left_margin(10); self.messages.set_right_margin(10)
        self.messages.set_top_margin(10)
        self.scroll = Gtk.ScrolledWindow(); self.scroll.add(self.messages)
        self.pack_start(self.scroll, True, True, 0)
        self.input = Gtk.Entry(placeholder_text='Ask about this file…')
        self.input.connect('activate', self.send)
        self.pack_start(self.input, False, False, 0)
        actions = Gtk.Box(spacing=8)
        self.send_button = Gtk.Button(label='Send')
        self.send_button.connect('clicked', self.send)
        actions.pack_start(self.send_button, False, False, 0)
        self.stop_button = Gtk.Button(label='Stop')
        self.stop_button.connect('clicked', lambda *_: self.client.stop() if self.client else None)
        self.stop_button.set_sensitive(False)
        actions.pack_start(self.stop_button, False, False, 0)
        self.pack_start(actions, False, False, 0)
        self.status = Gtk.Label(xalign=0); self.status.set_line_wrap(True)
        self.status.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.status.set_max_width_chars(42)
        self.pack_start(self.status, False, False, 0)
        self.preview_timer = GLib.timeout_add(400, self.preview)
        self.connect('destroy', self.close)
        self.set_busy(False)

    def close(self, *_):
        if self.client: self.client.stop()
        GLib.source_remove(self.preview_timer)

    def configure(self, number):
        scope = f'plancraft/plancraft:pr:{number}' if number else None
        if scope == self.scope:
            return
        if self.busy:
            raise RuntimeError('Stop the chat before switching PRs.')
        self.scope = scope
        self.client = PiChat(scope, self.checkout) if scope else None
        self.messages.get_buffer().set_text('')
        if self.client:
            for item in self.client.history():
                context = item.get('context') or {}
                anchor = context.get('head', '')[:10]
                self.append(f"{item['role'].capitalize()} · {anchor}\n{item['text']}\n\n")
        self.set_busy(False)

    def append(self, text):
        buffer = self.messages.get_buffer()
        buffer.insert(buffer.get_end_iter(), text)
        mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        self.messages.scroll_mark_onscreen(mark); buffer.delete_mark(mark)

    def preview(self):
        if not self.busy:
            try:
                context = self.context()
                rows = context.get('selection_diff_rows')
                selected = f'\nAttached: diff rows {rows[0]}–{rows[1]} (until cleared)' if rows else ''
                self.context_label.set_text(f"PR #{context['pr']} · {context['head'][:10]}\n{context.get('file') or 'PR overview'}{selected}")
            except ValueError as error:
                self.context_label.set_text(str(error))
        return True

    def set_busy(self, value):
        self.busy = value
        self.send_button.set_sensitive(self.client is not None and not value)
        self.input.set_sensitive(not value)
        self.stop_button.set_sensitive(value)
        self.busy_changed(value)

    def send(self, *_):
        if self.busy or self.client is None:
            return
        question = self.input.get_text().strip()
        if not question: return
        try:
            context = self.context()
        except ValueError as error:
            self.status.set_text(str(error)); return
        self.input.set_text('')
        self.set_busy(True)
        self.status.set_text('Connecting to Pi…')
        selected = '\nSelected code attached' if context.get('selection') else ''
        self.context_label.set_text(f"PR #{context['pr']} · {context['head'][:10]}\n{context.get('file') or 'PR overview'}{selected}")
        client = self.client
        def emit(kind, value):
            GLib.idle_add(self.event, kind, value, context)
        threading.Thread(target=client.ask, args=(context, question, emit), daemon=True).start()

    def event(self, kind, value, context):
        if kind == 'user':
            self.append(f"You · {context['head'][:10]} · {context.get('file') or 'overview'}\n{value}\n\nPi\n")
        elif kind == 'delta': self.append(value)
        elif kind == 'status': self.status.set_text(value)
        elif kind in ('done', 'error'):
            self.append('\n\n' if kind == 'done' else '\n[Incomplete] ' + value + '\n\n')
            self.status.set_text('Ready' if kind == 'done' else value)
            self.set_busy(False)
        return False
