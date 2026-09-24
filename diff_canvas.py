"""Open diff cards on a pannable, zoomable folder canvas."""
import re
import time
from review_progress import ReviewProgress
from pathlib import PurePosixPath
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, Pango, GLib

COLUMNS = ('DBTypes', 'Frontend', 'Shared/API', 'Backend', 'Other')
COLORS = {'A': ('#b9efcc', '#12572e'), 'D': ('#ffc4c4', '#791e28'),
          'M': ('#ffe8a3', '#674700')}


def group(path):
    parts = path.split('/')
    package = parts[1] if len(parts) > 2 and parts[0] == 'pkgs' else ''
    if package in ('dbtypes', 'db-types'):
        return 'DBTypes'
    if '-frontend' in package:
        return 'Frontend'
    if '-backend' in package:
        return 'Backend'
    if package.endswith('-shared'):
        return 'Shared/API'
    return 'Other'


def patch_lines(patch):
    old = new = None
    result = []
    for line in patch.splitlines():
        match = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
        if match:
            old, new = map(int, match.groups())
            # Hunk headers are patch metadata, not code. Keep parsing the
            # location, but do not show the technical header in the UI.
        elif old is not None and line[:1] in (' ', '+', '-'):
            kind = 'add' if line.startswith('+') else 'del' if line.startswith('-') else 'context'
            left = str(old) if kind != 'add' else ''
            right = str(new) if kind != 'del' else ''
            result.append((kind, f'{left:>5} {right:>5}  {line}'))
            old += kind != 'add'
            new += kind != 'del'
        elif old is not None or line.startswith(('Binary files', 'GIT binary', 'rename ', 'similarity ', 'old mode', 'new mode')):
            result.append(('context', line))
    return result or [('context', 'No text hunks (binary, rename, mode change, or empty file).')]


class DiffCanvas(Gtk.Box):
    def __init__(self, files, review_scope=None, progress_database=None, head=None, file_commits=None, open_file=None,
                 defer_render=False, reference_links=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.files = files
        self.reference_links = reference_links or {}
        self.open_file = open_file
        self.destroyed = False
        self.connect('destroy', lambda *_: setattr(self, 'destroyed', True))
        self.selected_context = None
        self.clearing_selection = False
        self.progress = ReviewProgress(review_scope, progress_database, head, file_commits)
        self.connect('destroy', lambda *_: self.progress.close())
        self.zoom = 1.0
        self.pending_zoom = None
        self.zoom_timer = None
        self.connect('destroy', self.cancel_zoom)
        self.drag = None
        bar = Gtk.Box(spacing=8)
        for title, factor in [('−', 1 / 1.2), ('+', 1.2), ('100%', None)]:
            button = Gtk.Button(label=title)
            button.connect('clicked', lambda _, f=factor: self.set_zoom(1.0 if f is None else self.zoom * f))
            bar.pack_start(button, False, False, 0)
        self.info = Gtk.Label(label='')
        self.info.set_ellipsize(Pango.EllipsizeMode.END)
        self.info.set_max_width_chars(30)
        bar.pack_start(self.info, False, False, 0)
        self.read_count = Gtk.Label()
        bar.pack_start(self.read_count, False, False, 8)
        help_text = 'Middle-drag to pan · Pinch or Ctrl + wheel to zoom · Hover filename for full path'
        help_label = Gtk.Label(label=help_text)
        help_label.set_ellipsize(Pango.EllipsizeMode.END)
        help_label.set_max_width_chars(24)
        help_label.set_tooltip_text(help_text)
        bar.pack_start(help_label, True, True, 12)
        self.pack_start(bar, False, False, 0)
        self.reference_mode = Gtk.CheckButton(label='Reference mode')
        self.reference_mode.connect('toggled', self.update_reference_output)
        self.pack_start(self.reference_mode, False, False, 8)
        self.search_hits = []
        self.search_index = -1
        self.search_bar = Gtk.Box(spacing=8)
        self.search_bar.set_no_show_all(True)
        self.search_entry = Gtk.SearchEntry(placeholder_text='Search changed text…')
        self.search_entry.connect('search-changed', self.search_changed)
        self.search_entry.connect('key-press-event', self.search_key)
        self.search_count = Gtk.Label(label='0 hits')
        self.search_bar.pack_start(self.search_entry, True, True, 0)
        self.search_bar.pack_start(self.search_count, False, False, 0)
        for label, callback in [('Previous', lambda *_: self.search_next(-1)), ('Next', lambda *_: self.search_next(1)), ('Close', self.close_search)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', callback)
            self.search_bar.pack_start(button, False, False, 0)
        self.pack_start(self.search_bar, False, False, 0)
        self.active_file = None
        self.focused_card = None
        self.cards = {}
        self.card_style = Gtk.CssProvider()
        self.card_positions = []
        self.rendering = False
        self.render_source = None
        self.render_iterator = None
        self.connect('destroy', self.cancel_render)
        location = Gtk.Box(spacing=16, margin=8)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.path_label = Gtk.Label(label='Move to a file to see its path', xalign=0, selectable=True)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.path_label.get_style_context().add_class('muted')
        self.file_label = Gtk.Label(xalign=0, selectable=True)
        labels.pack_start(self.path_label, False, False, 0)
        labels.pack_start(self.file_label, False, False, 0)
        location.pack_start(labels, True, True, 0)
        self.sticky_viewed = Gtk.CheckButton(label='Viewed (V)')
        self.sticky_viewed.set_sensitive(False)
        self.sticky_handler = self.sticky_viewed.connect('toggled', self.toggle_sticky)
        location.pack_start(self.sticky_viewed, False, False, 0)
        self.open_file_button = Gtk.Button(label='Open in Zed')
        self.open_file_button.set_sensitive(False)
        self.open_file_button.connect('clicked', self.open_focused_file)
        location.pack_start(self.open_file_button, False, False, 0)
        self.clear_selection_button = Gtk.Button(label='Clear selection')
        self.clear_selection_button.set_sensitive(False)
        self.clear_selection_button.connect('clicked', self.clear_selection)
        location.pack_start(self.clear_selection_button, False, False, 0)
        self.pack_start(location, False, False, 0)
        self.scroll = Gtk.ScrolledWindow()
        self.board = Gtk.Layout()
        self.scroll.add(self.board)
        self.viewport = Gtk.Overlay()
        self.viewport.add(self.scroll)
        self.pack_start(self.viewport, True, True, 0)
        self.bind_events(self.board)
        from install_pinch_zoom import install_pinch_zoom
        self.pinch = install_pinch_zoom(self)
        for adjustment in (self.scroll.get_hadjustment(), self.scroll.get_vadjustment()):
            adjustment.connect('value-changed', self.update_location)
            adjustment.connect('changed', self.update_location)
        if not defer_render:
            self.render()

    def open_search(self):
        self.search_bar.set_no_show_all(False)
        self.search_bar.show_all()
        self.search_entry.grab_focus()

    def close_search(self, *_):
        self.search_entry.set_text('')
        self.search_bar.hide()
        self.search_bar.set_no_show_all(True)

    def update_reference_output(self, *_):
        """Show compiler-resolved direct imports to files changed in this review."""
        if not self.reference_mode.get_active() or not self.active_file:
            return []
        selected_path = self.active_file[2]
        grouped = {}
        for link in self.reference_links.get(selected_path, ()):
            key = ('outgoing', link['to'], link['kind'], link.get('symbol', ''))
            grouped.setdefault(key, set()).add(link['line'])
        reverse_labels = {
            'calls': 'called by', 'imports from': 'imported by',
            'imports type from': 'type used by', 're-exports from': 're-exported by',
            'reads': 'read by', 'uses type': 'type used by',
        }
        for source, links in self.reference_links.items():
            for link in links:
                if link['to'] == selected_path:
                    kind = reverse_labels.get(link['kind'], link['kind'] + ' from')
                    key = ('incoming', source, kind, link.get('symbol', ''))
                    grouped.setdefault(key, set()).add(link['line'])
        lines = [f'Reference links for {selected_path}',
                 'Resolved from source in this PR checkout. Lines show where each link occurs.', '']
        if grouped:
            for (direction, target, kind, symbol), locations in sorted(grouped.items()):
                name = f' ({symbol})' if symbol else ''
                line_list = ', '.join(map(str, sorted(locations)))
                if direction == 'outgoing':
                    lines.append(f'  {selected_path}:{line_list} -- {kind}{name} --> {target}')
                else:
                    lines.append(f'  {selected_path} <-- {kind}{name} -- {target}:{line_list}')
        else:
            lines.append('No direct links to or from other changed files.')
        return lines

    def search_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close_search()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.search_next(-1 if event.state & Gdk.ModifierType.SHIFT_MASK else 1)
            return True
        return False

    def search_changed(self, *_):
        import re
        query = self.search_entry.get_text()
        self.search_hits = []
        if query:
            for file in self.files:
                content = '\n'.join(line for _, line in patch_lines(file[3]))
                self.search_hits.extend((file[2], match.start(), match.end())
                                        for match in re.finditer(re.escape(query), content, re.IGNORECASE))
        self.search_index = -1
        self.render()
        self.search_count.set_text(f'{len(self.search_hits)} hits')
        if self.search_hits:
            self.search_next(1)

    def search_next(self, step):
        if not self.search_hits:
            return
        self.search_index = (self.search_index + step) % len(self.search_hits)
        path, start, end = self.search_hits[self.search_index]
        self.search_count.set_text(f'{self.search_index + 1} / {len(self.search_hits)}')
        self.jump_to_file(path)
        def reveal():
            if self.destroyed:
                return False
            for widget in self.cards[path].get_children():
                if isinstance(widget, Gtk.TextView):
                    buffer = widget.get_buffer()
                    location = widget.get_iter_location(buffer.get_iter_at_offset(start))
                    y = next(y for file, x, y, w, h in self.card_positions if file[2] == path)
                    self.scroll.get_vadjustment().set_value(y + widget.get_allocation().y + location.y)
                    buffer.remove_tag_by_name('search-current', *buffer.get_bounds())
                    buffer.apply_tag_by_name('search-current', buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end))
            return False
        GLib.idle_add(reveal)

    def update_location(self, *_):
        if self.rendering or getattr(self, 'destroyed', False):
            return
        horizontal, vertical = self.scroll.get_hadjustment(), self.scroll.get_vadjustment()
        left, top = horizontal.get_value(), vertical.get_value()
        right, bottom = left + horizontal.get_page_size(), top + vertical.get_page_size()
        center_x, center_y = (left + right) / 2, (top + bottom) / 2
        visible = []
        for file, x, y, width, height in self.card_positions:
            overlap = min(right, x + width) - max(left, x)
            if overlap > 0 and y < bottom and y + height > top:
                # Distance to the card edges, not its midpoint: a huge file
                # containing the viewport center should still win.
                dx = max(x - center_x, center_x - (x + width), 0)
                dy = max(y - center_y, center_y - (y + height), 0)
                visible.append((dx * dx + dy * dy, -overlap, x, file))
        self.active_file = min(visible, key=lambda item: item[:3])[3] if visible else None
        focused = self.cards.get(self.active_file[2]) if self.active_file else None
        if focused is not self.focused_card:
            if self.focused_card is not None:
                self.focused_card.get_style_context().remove_class('focused-card')
            if focused is not None:
                focused.get_style_context().add_class('focused-card')
            self.focused_card = focused
        self.sticky_viewed.handler_block(self.sticky_handler)
        if self.active_file:
            path = PurePosixPath(self.active_file[2])
            self.path_label.set_text(' › '.join(path.parts[:-1]) or '(repository root)')
            self.path_label.set_tooltip_text(str(path))
            self.file_label.set_text(path.name)
            self.sticky_viewed.set_active(self.progress.is_viewed(self.active_file))
        else:
            self.path_label.set_text('No file in view')
            self.path_label.set_tooltip_text(None)
            self.file_label.set_text('')
            self.sticky_viewed.set_active(False)
        self.sticky_viewed.set_sensitive(self.active_file is not None)
        self.sticky_viewed.handler_unblock(self.sticky_handler)
        if hasattr(self, 'update_reference_output'):
            self.update_reference_output()
        self.open_file_button.set_sensitive(bool(
            self.open_file and self.active_file and not self.active_file[0].startswith('D')))

    def open_focused_file(self, *_):
        if self.open_file and self.active_file and not self.active_file[0].startswith('D'):
            self.open_file(self.active_file[2])

    def capture_selection(self, buffer, _location, _mark, file):
        if self.clearing_selection:
            return
        bounds = buffer.get_selection_bounds()
        if not bounds:
            return
        start, end = bounds
        self.selected_context = dict(file=file, text=buffer.get_text(start, end, True),
                                     first=start.get_line()+1,
                                     last=end.get_line()+(1 if end.get_line_offset() else 0))
        self.clear_selection_button.set_sensitive(True)
        self.clear_selection_button.set_tooltip_text('Attached to chat: ' + file[2])

    def clear_selection(self, *_):
        self.clearing_selection = True
        self.selected_context = None
        for card in self.cards.values():
            for child in card.get_children():
                if isinstance(child, Gtk.TextView):
                    buffer = child.get_buffer()
                    buffer.place_cursor(buffer.get_start_iter())
        self.clearing_selection = False
        self.clear_selection_button.set_sensitive(False)
        self.clear_selection_button.set_tooltip_text(None)

    def toggle_sticky(self, button):
        if self.active_file is None:
            return
        path = self.active_file[2]
        try:
            self.progress.set_viewed(self.active_file, button.get_active())
        except Exception as error:
            self.read_count.set_text('Could not save viewed marker: ' + str(error))
            self.update_location()
            return
        self.render()
        self.jump_to_file(path)

    def jump_to_file(self, path):
        def jump():
            if self.destroyed:
                return False
            for file, x, y, width, height in self.card_positions:
                if file[2] != path:
                    continue
                horizontal = self.scroll.get_hadjustment()
                vertical = self.scroll.get_vadjustment()
                # Leave enough space below the last card to align its header.
                board_width, board_height = self.board.get_size()
                self.board.set_size(board_width, max(board_height, round(y + vertical.get_page_size())))
                vertical.set_upper(max(vertical.get_upper(), y + vertical.get_page_size()))
                horizontal.set_value(x)
                vertical.set_value(y)
                self.update_location()
                break
            return False
        GLib.idle_add(jump)

    def bind_events(self, widget):
        widget.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                          Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK |
                          Gdk.EventMask.SMOOTH_SCROLL_MASK)
        widget.connect('button-press-event', self.press)
        widget.connect('button-release-event', self.release)
        widget.connect('motion-notify-event', self.motion)
        widget.connect('scroll-event', self.wheel)

    def press(self, widget, event):
        if event.button == 2:
            self.drag = (event.x_root, event.y_root)
            return True
        return False

    def release(self, widget, event):
        if event.button == 2:
            self.drag = None
            return True
        return False

    def motion(self, widget, event):
        if self.drag:
            x, y = self.drag
            for adj, delta in ((self.scroll.get_hadjustment(), x-event.x_root),
                               (self.scroll.get_vadjustment(), y-event.y_root)):
                adj.set_value(adj.get_value()+delta)
            self.drag = (event.x_root, event.y_root)
            return True
        return False

    def wheel(self, widget, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        delta = -1 if event.direction == Gdk.ScrollDirection.UP else 1
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _, _, delta = event.get_scroll_deltas()
        if delta:
            target = self.pending_zoom if self.pending_zoom is not None else self.zoom
            self.queue_zoom(target * 1.08 ** (-max(-5, min(5, delta))))
        return True

    def cancel_zoom(self, *_):
        if self.zoom_timer is not None:
            GLib.source_remove(self.zoom_timer)
            self.zoom_timer = None
        self.pending_zoom = None

    def queue_zoom(self, value):
        self.cancel_zoom()
        self.pending_zoom = max(.15, min(2.5, value))
        self.info.set_text(f'{round(self.pending_zoom*100)}% · Zooming…')
        self.zoom_timer = GLib.timeout_add(120, self.apply_pending_zoom)

    def apply_pending_zoom(self):
        value = self.pending_zoom
        self.zoom_timer = None
        self.pending_zoom = None
        if value is not None:
            self.set_zoom(value)
        return False

    def set_zoom(self, value):
        self.cancel_zoom()
        value = max(.15, min(2.5, value))
        ratio = value / self.zoom
        adjustments = [self.scroll.get_hadjustment(), self.scroll.get_vadjustment()]
        positions = [(a.get_value()+a.get_page_size()/2)*ratio-a.get_page_size()/2 for a in adjustments]
        self.zoom = value
        self.render()
        def restore():
            for adj, position in zip(adjustments, positions):
                adj.set_value(max(0, position))
            return False
        GLib.idle_add(restore)

    def style(self, widget, background, foreground):
        css = Gtk.CssProvider()
        css.load_from_data(f'* {{ background-color: {background}; color: {foreground}; font-size: {max(3, 11*self.zoom)}pt; }}'.encode())
        widget.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+1)

    def label(self, text, bg='#e4e9f2', fg='#26334a'):
        label = Gtk.Label(label=text, xalign=0)
        label.set_margin_start(max(2, round(8*self.zoom)))
        label.set_margin_end(max(2, round(8*self.zoom)))
        self.style(label, bg, fg)
        return label

    def put(self, widget, x, y):
        self.board.put(widget, round(x), round(y))
        widget.show_all()
        minimum, natural = widget.get_preferred_size()
        return natural.width, natural.height

    def connector(self, parent, x, y, color, column, prefix):
        px, py = parent
        # Two thin widgets draw a folder branch without a giant bitmap.
        for left, top, width, height in ((px, py, 2, max(2, y-py)), (px, y, max(2, x-px), 2)):
            line = Gtk.EventBox()
            self.style(line, color, color)
            line.set_size_request(round(width), round(height))
            self.put(line, left, top)
            self.bind_events(line)
            self.folder_widgets.append((line, column, prefix))

    def card(self, status, old, path, patch):
        key = status[0] if status[0] in COLORS else 'M'
        bg, fg = COLORS[key]
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.get_style_context().add_class('diff-card')
        frame.get_style_context().add_provider(self.card_style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+1)
        self.cards[path] = frame
        header = Gtk.Box(spacing=6)
        title = self.label(f"{status}  {PurePosixPath(path).name}", bg, fg)
        title.set_tooltip_text(path if old == path else f'{old} → {path}')
        title_event = Gtk.EventBox()
        title_event.add(title)
        header.pack_start(title_event, True, True, 0)
        viewed = Gtk.CheckButton(label='Viewed')
        file = (status, old, path, patch)
        viewed.set_active(self.progress.is_viewed(file))
        header.pack_start(viewed, False, False, 0)
        copy = Gtk.Button(label='⧉')
        copy.set_tooltip_text('Copy full path')
        copy.connect('clicked', lambda *_: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(path, -1))
        header.pack_start(copy, False, False, 0)
        self.style(header, bg, fg)
        # App-wide button sizes are for toolbars, not zoomable cards.
        controls = Gtk.CssProvider()
        controls.load_from_data(f'''
            * {{ font-size: {max(3, 11*self.zoom)}pt; }}
            button, checkbutton {{
                min-width: 0; min-height: 0;
                padding: {max(1, round(3*self.zoom))}px;
                margin: 0; border: 0; border-radius: 0;
                background-image: none; background-color: transparent;
                box-shadow: none; color: {fg};
            }}
            button:hover {{ background-color: alpha({fg}, 0.12); }}
            check {{
                min-width: {max(4, round(12*self.zoom))}px;
                min-height: {max(4, round(12*self.zoom))}px;
                padding: 0; margin: 0;
            }}
        '''.encode())
        for control in (viewed, copy):
            control.set_valign(Gtk.Align.CENTER)
            control.get_style_context().add_provider(
                controls, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+2)
        header.set_spacing(max(1, round(6*self.zoom)))
        frame.pack_start(header, False, False, 0)
        self.bind_events(title_event)
        title_event.connect('button-press-event', lambda *_args, p=path: self.select_reference_file(p))
        title_event.set_tooltip_text('Click to select this file for reference analysis')
        def toggle(button):
            try:
                self.progress.set_viewed(file, button.get_active())
            except Exception as error:
                button.handler_block(handler)
                button.set_active(not button.get_active())
                button.handler_unblock(handler)
                self.read_count.set_text('Could not save viewed marker: ' + str(error))
                return
            self.render()
            self.jump_to_file(path)
        handler = viewed.connect('toggled', toggle)
        if viewed.get_active() and not any(hit[0] == path for hit in self.search_hits):
            return frame, fg
        text = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.style(text, '#f7f9fc', '#253247')
        font = Pango.FontDescription('Monospace')
        font.set_size(round(max(3, 11*self.zoom)*Pango.SCALE))
        text.override_font(font)
        buffer = text.get_buffer()
        buffer.create_tag('font', font_desc=font)
        for name, background, foreground in [('add','#c6efd2','#114a25'), ('del','#ffd0d0','#771a25'),
                                               ('context','#f7f9fc','#253247'), ('hunk','#e1e9fa','#344c77')]:
            buffer.create_tag(name, paragraph_background=background, foreground=foreground)
        lines = patch_lines(patch)
        content = '\n'.join(line for _, line in lines)
        for index, (kind, line) in enumerate(lines):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), line + ('\n' if index < len(lines)-1 else ''), kind, 'font')
        buffer.create_tag('search-hit', background='#ffe69a', foreground='#202c42')
        buffer.create_tag('search-current', background='#ffb84d', foreground='#202c42')
        for hit_path, start, end in self.search_hits:
            if hit_path == path:
                buffer.apply_tag_by_name('search-hit', buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end))
        measure = text.create_pango_layout(content)
        measure.set_font_description(font)
        width, height = measure.get_pixel_size()
        column_measure = text.create_pango_layout('0' * 300)
        column_measure.set_font_description(font)
        max_width = column_measure.get_pixel_size()[0]
        if width > max_width:
            measure.set_width(max_width * Pango.SCALE)
            measure.set_wrap(Pango.WrapMode.WORD_CHAR)
            _, height = measure.get_pixel_size()
            width = max_width
        text.set_size_request(max(round(540*self.zoom), width+24), height)
        buffer.connect('mark-set', self.capture_selection, file)
        self.bind_events(text)
        frame.pack_start(text, False, False, 0)
        # TextView validates its text layout during its first allocation. GTK
        # can allocate the old height even though the preferred height is now
        # smaller. Request one fresh allocation after that validation.
        def allocated(widget, allocation):
            widget.disconnect(allocation_handler)
            def resize():
                if widget.get_parent() is self.board:
                    widget.queue_resize()
                return False
            GLib.idle_add(resize)
        allocation_handler = frame.connect('size-allocate', allocated)
        return frame, fg

    def select_reference_file(self, path):
        file = next((item for item in self.files if item[2] == path), None)
        if file:
            self.active_file = file
            self.update_reference_output()
        return False

    def update_read_count(self):
        count = sum(self.progress.is_viewed(file) for file in self.files)
        self.read_count.set_text(f'✓ {count} / {len(self.files)} viewed')

    def cancel_render(self, *_):
        if self.render_source is not None:
            GLib.source_remove(self.render_source)
            self.render_source = None
        if self.render_iterator is not None:
            self.render_iterator.close()
            self.render_iterator = None
        self.rendering = False

    def render(self):
        self.cancel_render()
        for _ in self._render_steps():
            pass

    def render_incrementally(self, progress, done, failed):
        """Yield to GTK between card batches so progress and Cancel stay usable."""
        self.cancel_render()
        self.render_iterator = self._render_steps()
        print(f'Drawing diff map: {len(self.files)} changed files.')
        def tick():
            try:
                deadline = time.monotonic() + .02
                while time.monotonic() < deadline:
                    count, path = next(self.render_iterator)
                    print(f'Drawn {count}/{len(self.files)} file cards: {path}')
                    progress(count, len(self.files), path)
            except StopIteration:
                self.render_source = self.render_iterator = None
                print('Diff map drawing complete.')
                try:
                    done()
                except Exception as error:
                    failed(str(error))
                return False
            except Exception as error:
                import traceback
                traceback.print_exc()
                self.render_source = self.render_iterator = None
                self.rendering = False
                failed(str(error))
                return False
            return True
        self.render_source = GLib.idle_add(tick)

    def _render_steps(self):
        self.card_style.load_from_data(f'''
            .diff-card {{ border: {max(1, round(3*self.zoom))}px solid transparent; }}
            .diff-card.focused-card {{ border-color: #365bd6; }}
            .diff-card.dependency-selected {{ border-color: #862eb8; }}
        '''.encode())
        self.rendering = True
        self.focused_card = None
        self.cards = {}
        self.card_positions = []
        self.folder_widgets = []
        for child in self.board.get_children():
            self.board.remove(child)
        z = self.zoom
        column_x = 40*z
        bottom = 800*z
        processed = 0
        for column in COLUMNS:
            entries = sorted((f for f in self.files if group(f[2]) == column), key=lambda f: f[2])
            if not entries:
                continue
            self.put(self.label(column.upper()), column_x, 25*z)
            y = 85*z
            right = column_x + 600*z
            folders = {}
            for status, old, path, patch in entries:
                parts = PurePosixPath(path).parts
                for depth in range(1, len(parts)):
                    prefix = parts[:depth]
                    if prefix not in folders:
                        x = column_x + (depth-1)*28*z
                        node = self.label(parts[depth-1])
                        node.set_tooltip_text('/'.join(prefix))
                        if prefix[:-1] in folders:
                            self.connector(folders[prefix[:-1]], x, y+10*z, '#9daac0', column, '/'.join(prefix))
                        _, h = self.put(node, x, y)
                        self.folder_widgets.append((node, column, '/'.join(prefix)))
                        folders[prefix] = (x+12*z, y+h)
                        y += h+18*z
                x = column_x + (len(parts)-1)*28*z
                card, color = self.card(status, old, path, patch)
                if parts[:-1] in folders:
                    self.connector(folders[parts[:-1]], x, y+10*z, color, column, path)
                w, h = self.put(card, x, y)
                self.card_positions.append(((status, old, path, patch), x, y, w, h))
                right = max(right, x+w)
                y += h+40*z
                processed += 1
                yield processed, path
            bottom = max(bottom, y)
            column_x = right+100*z
        self.board.set_size(round(column_x+400*z), round(bottom+500*z))
        self.info.set_text(f'{round(z*100)}% · Green added / Red deleted / Yellow modified')
        self.update_read_count()
        if getattr(self, 'on_viewed_changed', None):
            self.on_viewed_changed()
        self.rendering = False
        print(f'Diff map rendered: {len(self.files)} cards.')
        self.update_location()
