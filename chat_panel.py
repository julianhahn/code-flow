"""GTK chat panel; all Pi I/O stays on a worker thread."""
import threading
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Pango, Gdk
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
        header = Gtk.Box(spacing=8)
        header.pack_start(Gtk.Label(label='Ask Pi · Read-only', xalign=0), True, True, 0)
        self.copy_button = Gtk.Button(label='Copy chat')
        self.copy_button.set_tooltip_text('Copy this chat as plain text')
        self.copy_button.connect('clicked', self.copy_history)
        header.pack_end(self.copy_button, False, False, 0)
        self.pack_start(header, False, False, 0)
        self.model_label = Gtk.Label(label='Model / effort: checked when sending', xalign=0)
        self.model_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.model_label.get_style_context().add_class('muted')
        self.pack_start(self.model_label, False, False, 0)
        self.context_label = Gtk.Label(label='Select a PR, then open its diff map.', xalign=0)
        self.context_label.set_line_wrap(True)
        self.context_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.context_label.set_max_width_chars(42)
        self.context_label.set_lines(3)
        self.context_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.context_label.set_yalign(0)
        self.context_label.connect('style-updated', self.size_context)
        self.context_label.connect('notify::label', self.context_tooltip)
        self.size_context(self.context_label)
        self.context_tooltip(self.context_label)
        self.pack_start(self.context_label, False, False, 0)
        self.messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=8)
        self.reply_body = None
        self.reply_text = ''
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.add(self.messages)
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

    def size_context(self, label):
        layout = label.create_pango_layout('Ag\nAg\nAg')
        label.set_size_request(-1, layout.get_pixel_size()[1])

    def context_tooltip(self, label, *_):
        text = label.get_text()
        # Pango limits lines per paragraph; use one paragraph for a total cap.
        label.handler_block_by_func(self.context_tooltip)
        label.set_text(text.replace('\n', ' · '))
        label.handler_unblock_by_func(self.context_tooltip)
        label.set_tooltip_text(text)

    def copy_history(self, *_):
        if not self.scope:
            self.status.set_text('Select a PR to copy its chat.')
            return
        lines = ['Code Flow chat', self.scope, '']
        for card in self.messages.get_children():
            children = card.get_children()
            if not children:
                continue
            role = children[0].get_text()
            body = children[1] if len(children) > 1 else None
            lines.append(role)
            if body:
                for block in body.get_children():
                    widget = block.get_child() if isinstance(block, Gtk.ScrolledWindow) else block
                    if isinstance(widget, Gtk.Box) and widget.get_style_context().has_class('chat-code'):
                        code = widget.get_children()[-1]
                        lines.extend(['```', code.get_text(), '```'])
                    elif isinstance(widget, Gtk.Label):
                        lines.append(widget.get_text())
            lines.append('')
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text('\\n'.join(lines).strip() + '\\n', -1)
        self.status.set_text('Chat copied to clipboard.')

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
        self.model_label.set_text('Model / effort: checked when sending')
        self.client = PiChat(scope, self.checkout) if scope else None
        for child in self.messages.get_children():
            child.destroy()
        self.reply_body = None
        self.reply_text = ''
        if self.client:
            for item in self.client.history():
                context = item.get('context') or {}
                anchor = context.get('head', '')[:10]
                body = self.message_card(item['role'], item['text'], anchor)
                if item['role'] == 'assistant':
                    self.runtime_label(body, item.get('runtime') or {})
        self.set_busy(False)

    @staticmethod
    def runtime_text(runtime):
        model = '/'.join(part for part in (runtime.get('provider'), runtime.get('model')) if part) or 'unknown'
        return f"{runtime.get('source', 'used').title()}: {model} · Effort (Pi setting): {runtime.get('effort') or 'unknown'}"

    def runtime_label(self, body, runtime):
        card = body.get_parent()
        label = getattr(card, 'runtime_label', None)
        if label is None:
            label = Gtk.Label(xalign=0, selectable=True)
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.get_style_context().add_class('muted')
            card.pack_start(label, False, False, 0)
            card.runtime_label = label
        text = self.runtime_text(runtime)
        label.set_text(text)
        label.set_tooltip_text(text)
        label.show()

    def message_card(self, role, text='', context=''):
        user = role == 'user'
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=2)
        card.get_style_context().add_class('chat-user' if user else 'chat-agent')
        heading = Gtk.Label(label=('You' if user else 'Agent') + (' · ' + context if context else ''), xalign=0)
        heading.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        heading.get_style_context().add_class('chat-role')
        card.pack_start(heading, False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.pack_start(body, False, False, 0)
        self.messages.pack_start(card, False, False, 0)
        self.render_message(body, text)
        card.show_all()
        return body

    def render_message(self, body, text):
        for child in body.get_children():
            child.destroy()
        # Fence-aware rendering: unfinished streamed fences remain code blocks.
        blocks = []
        lines = []
        language = ''
        code = False
        for line in text.splitlines(keepends=True):
            if line.lstrip().startswith('```'):
                if lines:
                    blocks.append((code, language, ''.join(lines)))
                lines = []
                language = line.strip()[3:].strip() if not code else ''
                code = not code
            else:
                lines.append(line)
        if lines:
            blocks.append((code, language, ''.join(lines)))
        for is_code, language, content in blocks:
            if is_code:
                block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                block.get_style_context().add_class('chat-code')
                bar = Gtk.Box(spacing=8)
                bar.pack_start(Gtk.Label(label=language or 'Code', xalign=0), True, True, 0)
                copy = Gtk.Button(label='Copy')
                copy.connect('clicked', lambda _, value=content: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(value, -1))
                bar.pack_end(copy, False, False, 0)
                block.pack_start(bar, False, False, 0)
                label = Gtk.Label(label=content.rstrip('\n'), xalign=0, selectable=True)
                label.get_style_context().add_class('code-text')
                block.pack_start(label, False, False, 0)
                scroll = Gtk.ScrolledWindow()
                scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
                scroll.add(block)
                body.pack_start(scroll, False, False, 0)
            else:
                label = Gtk.Label(label=content.strip(), xalign=0, selectable=True)
                label.set_line_wrap(True)
                label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(44)
                body.pack_start(label, False, False, 0)
        body.show_all()

    def append(self, text):
        if self.reply_body is None:
            self.reply_body = self.message_card('assistant')
            self.reply_text = ''
        adjustment = self.scroll.get_vadjustment()
        at_bottom = adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 40
        self.reply_text += text
        self.render_message(self.reply_body, self.reply_text)
        if at_bottom:
            def scroll_down():
                adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
                return False
            GLib.idle_add(scroll_down)

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
        self.model_label.set_text('Checking model and effort…')
        selected = '\nSelected code attached' if context.get('selection') else ''
        self.context_label.set_text(f"PR #{context['pr']} · {context['head'][:10]}\n{context.get('file') or 'PR overview'}{selected}")
        client = self.client
        def emit(kind, value):
            GLib.idle_add(self.event, kind, value, context)
        threading.Thread(target=client.ask, args=(context, question, emit), daemon=True).start()

    def event(self, kind, value, context):
        if kind == 'user':
            self.message_card('user', value, f"{context['head'][:10]} · {context.get('file') or 'overview'}")
            self.reply_body = self.message_card('assistant')
            self.reply_text = ''
        elif kind == 'runtime':
            text = self.runtime_text(value)
            self.model_label.set_text(text)
            self.model_label.set_tooltip_text(text)
            if self.reply_body is not None:
                self.runtime_label(self.reply_body, value)
        elif kind == 'delta': self.append(value)
        elif kind == 'status': self.status.set_text(value)
        elif kind in ('done', 'error'):
            self.append('\n\n' if kind == 'done' else '\n[Incomplete] ' + value + '\n\n')
            self.status.set_text('Ready' if kind == 'done' else value)
            self.set_busy(False)
        return False
