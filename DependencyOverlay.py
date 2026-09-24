"""Optional dependency drawing and selection focus for the existing diff canvas."""
import math
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango
from DependencyGraph import DependencyGraph


class DependencyOverlay:
    MAX_ARROWS = 500

    def __init__(self, canvas, head=None, open_source=None):
        self.canvas, self.head = canvas, head
        self.open_source = open_source
        self.graph = DependencyGraph()
        self.loaded = False
        self.selected = None
        self.symbol = None
        self.routes = []
        self.failure = None
        self.dialogs = []
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bar = Gtk.Box(spacing=8)
        self.controls.pack_start(bar, False, False, 0)
        self.imports = Gtk.CheckButton(label='Imports')
        self.references = Gtk.CheckButton(label='References')
        self.show_all = Gtk.CheckButton(label='Show all links')
        for button in (self.imports, self.references, self.show_all):
            bar.pack_start(button, False, False, 0)
            button.connect('toggled', self.changed)
        self.evidence_button = Gtk.Button(label='Connections (0)')
        self.evidence_button.connect('clicked', lambda *_: self.show_evidence())
        self.clear_button = Gtk.Button(label='Clear focus')
        self.clear_button.connect('clicked', lambda *_: self.select(None))
        for button in (self.evidence_button, self.clear_button):
            bar.pack_start(button, False, False, 0)
        self.status = Gtk.Label(label='Links off · Blue dashed: imports · Purple dotted: references', xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.set_single_line_mode(True)
        self.status.set_max_width_chars(70)
        self.controls.pack_start(self.status, False, False, 0)
        self.summary = 'Select a file to focus its direct links. Arrows point from use to definition.'
        canvas.pack_start(self.controls, False, False, 0)
        canvas.reorder_child(self.controls, len(canvas.get_children()) - 2)
        self.layer = Gtk.DrawingArea()
        self.layer.set_hexpand(True)
        self.layer.set_vexpand(True)
        canvas.viewport.add_overlay(self.layer)
        canvas.viewport.set_overlay_pass_through(self.layer, True)
        self.layer.connect('draw', self.draw)
        for adjustment in (canvas.scroll.get_hadjustment(), canvas.scroll.get_vadjustment()):
            adjustment.connect('value-changed', lambda *_: self.layer.queue_draw())
        canvas.connect('destroy', lambda *_: self.dispose())
        self.update()

    @property
    def kinds(self):
        return {kind for kind, button in (('import', self.imports), ('reference', self.references)) if button.get_active()}

    def message(self, text):
        self.status.set_text(' '.join(text.split())[:600])
        self.status.set_tooltip_text(text)

    def changed(self, *_):
        if 'reference' not in self.kinds:
            self.symbol = None
        self.update()

    def select(self, path, symbol=None):
        if path is not None and not self.kinds:
            return
        self.selected, self.symbol = path, symbol
        self.update()

    def visible_links(self):
        links = self.graph.links(self.kinds, self.selected, self.symbol, self.show_all.get_active())
        if self.selected is None:
            links = [edge for edge in links if edge['source'] in self.canvas.cards or edge['target'] in self.canvas.cards]
        return links

    def update(self):
        canvas = self.canvas
        # Never dim the map while loading, or when no link layer is enabled.
        focused = self.loaded and bool(self.kinds) and self.selected is not None
        neighbours = self.graph.neighbours(self.selected, self.kinds, self.symbol) if focused else set()
        for path, card in canvas.cards.items():
            card.set_opacity(1 if not focused or path in neighbours else .35)
            style = card.get_style_context()
            style.remove_class('dependency-selected')
            if focused and path == self.selected:
                style.add_class('dependency-selected')
        for widget, column, prefix in getattr(canvas, 'folder_widgets', []):
            relevant = any(canvas.dependency_group(path) == column and
                           (path == prefix or path.startswith(prefix + '/')) for path in neighbours)
            widget.set_opacity(1 if not focused or relevant else .35)
        links = self.visible_links()
        self.evidence_button.set_label(f'Connections ({len(links)})')
        self.evidence_button.set_sensitive(bool(links) or self.loaded)
        self.clear_button.set_sensitive(self.selected is not None)
        self.rebuild_routes(links)
        if self.failure:
            self.message('Links unavailable: ' + self.failure + ' · Reopen the diff map to retry.')
        else:
            if not self.kinds:
                text = 'Links off · Blue dashed: imports · Purple dotted: references'
            elif not self.loaded:
                text = 'Links are prepared automatically when the diff map is built.'
            else:
                text = self.summary
                if self.selected:
                    text += ' · Focus: ' + self.selected
                    if self.symbol:
                        text += ' (one symbol)'
                    selected_file = next((f for f in canvas.files if f[2] == self.selected), None)
                    if selected_file and selected_file[0].startswith('D'):
                        text += ' · Deleted: no head-version links'
                    elif self.selected not in self.graph.data.get('coverage', {}).get('analysedFiles', []):
                        text += ' · File outside analysed coverage'
                if self.clipped:
                    text += f' · First {self.MAX_ARROWS} arrows shown; all links are in Connections'
                outside = sum(edge['source'] not in canvas.cards or edge['target'] not in canvas.cards for edge in links)
                if outside:
                    text += f' · {outside} connections include files outside this map'
            self.message(text)
        self.layer.queue_draw()

    def rebuild_routes(self, links=None):
        canvas = self.canvas
        positions = {file[2]: (x, y, w, h) for file, x, y, w, h in canvas.card_positions}
        rails = {}
        for path, (x, y, w, h) in positions.items():
            column = canvas.dependency_group(path)
            rails[column] = max(rails.get(column, 0), x + w)
        links = self.visible_links() if links is None else links
        key = lambda edge: (edge['source'], edge['target'], edge['kind'])
        self.active_edges = {key(edge) for edge in links}
        if self.selected is not None and self.show_all.get_active():
            # Keep unrelated arrows faint in Show all mode. Focused links come
            # first so the drawing cap never favours background connections.
            links = links + [edge for edge in self.graph.links(self.kinds, show_all=True)
                             if key(edge) not in self.active_edges]
        drawable = [edge for edge in links if edge['source'] in positions and edge['target'] in positions]
        self.clipped = len(drawable) > self.MAX_ARROWS
        self.routes = []
        for index, edge in enumerate(drawable[:self.MAX_ARROWS]):
            source, target = edge['source'], edge['target']
            route = self.graph.route(positions[source], positions[target],
                                     rails[canvas.dependency_group(source)], rails[canvas.dependency_group(target)],
                                     index, canvas.zoom)
            self.routes.append((edge, route))

    def draw(self, widget, cr):
        horizontal = self.canvas.scroll.get_hadjustment().get_value()
        vertical = self.canvas.scroll.get_vadjustment().get_value()
        cr.save()
        cr.rectangle(0, 0, self.canvas.scroll.get_hadjustment().get_page_size(),
                     self.canvas.scroll.get_vadjustment().get_page_size())
        cr.clip()
        cr.translate(-horizontal, -vertical)
        for edge, route in self.routes:
            if not route:
                continue
            color = (.12, .36, .84) if edge['kind'] == 'import' else (.53, .18, .72)
            opacity = 1 if (edge['source'], edge['target'], edge['kind']) in self.active_edges else .18
            cr.set_source_rgba(*color, .94 * opacity)
            cr.set_line_width(max(1.5, 2 * self.canvas.zoom))
            cr.set_dash([8, 5] if edge['kind'] == 'import' else [2, 5])
            cr.move_to(*route[0])
            for point in route[1:]:
                cr.line_to(*point)
            cr.stroke()
            cr.set_dash([])
            ax, ay = route[-2]
            x, y = route[-1]
            angle = math.atan2(y - ay, x - ax)
            size = max(6, 8 * self.canvas.zoom)
            cr.move_to(x, y)
            cr.line_to(x - size * math.cos(angle - .45), y - size * math.sin(angle - .45))
            cr.line_to(x - size * math.cos(angle + .45), y - size * math.sin(angle + .45))
            cr.close_path()
            cr.fill()
            # Counts sit in the source gutter, never over code.
            if len(edge['evidence']) > 1:
                x, y = route[1]
                label = str(len(edge['evidence']))
                cr.set_font_size(11)
                extents = cr.text_extents(label)
                cr.set_source_rgba(1, 1, 1, .95 * opacity)
                cr.rectangle(x + 3, y - 13, extents.width + 5, 15)
                cr.fill()
                cr.set_source_rgba(*color, opacity)
                cr.move_to(x + 5, y - 2)
                cr.show_text(label)
        cr.restore()
        return False

    def hit(self, event):
        if not self.layer.get_realized():
            return None
        _, ox, oy = self.layer.get_window().get_origin()
        x = event.x_root - ox + self.canvas.scroll.get_hadjustment().get_value()
        y = event.y_root - oy + self.canvas.scroll.get_vadjustment().get_value()
        closest = min(((self.graph.distance((x, y), route), edge) for edge, route in self.routes),
                      default=(float('inf'), None), key=lambda pair: pair[0])
        return closest[1] if closest[0] <= 7 else None

    def accept(self, data):
        self.failure = None
        self.graph = DependencyGraph(data)
        self.loaded = True
        coverage = data['coverage']
        state = 'Project coverage checked' if coverage['complete'] else 'INCOMPLETE coverage'
        self.summary = (f"{state} · {coverage['files']} files · TypeScript {data['typescript']} · {self.head[:10] if self.head else 'head'}"
                        ' · Blue dashed: imports · Purple dotted: references')
        self.update()

    def unavailable(self, message):
        self.reset()
        self.failure = message
        self.update()

    def reset(self):
        self.loaded = False
        self.failure = None
        self.selected = self.symbol = None
        self.graph = DependencyGraph()
        self.dispose()
        self.update()

    def dispose(self):
        for dialog in list(self.dialogs):
            dialog.destroy()

    def show_evidence(self, edge=None):
        links = [edge] if edge else self.visible_links()
        parent = self.canvas.get_toplevel()
        dialog = Gtk.Dialog(title='Dependency connections', transient_for=parent if isinstance(parent, Gtk.Window) else None)
        dialog.set_default_size(1000, 480)
        dialog.add_button('Close', Gtk.ResponseType.CLOSE)
        dialog.connect('response', lambda *_: dialog.destroy())
        self.dialogs.append(dialog)
        dialog.connect('destroy', lambda widget: self.dialogs.remove(widget) if widget in self.dialogs else None)
        content = dialog.get_content_area()
        coverage = self.graph.data.get('coverage', {})
        issues = coverage.get('issues', [])
        title = Gtk.Label(label='Static code links, not runtime order. Files marked “outside map” remain in the list only.', xalign=0, margin=8)
        title.set_line_wrap(True)
        content.pack_start(title, False, False, 0)
        if issues:
            warning = Gtk.Expander(label=f"Incomplete coverage: {coverage.get('issueCount', len(issues))} notices")
            scroll = Gtk.ScrolledWindow()
            scroll.set_size_request(-1, 100)
            text = '\n'.join(issues)
            if coverage.get('uncovered'):
                text += '\nOutside project coverage:\n' + '\n'.join(coverage['uncovered'])
            label = Gtk.Label(label=text, xalign=0, selectable=True)
            label.set_line_wrap(True)
            scroll.add(label)
            warning.add(scroll)
            content.pack_start(warning, False, False, 0)
        rows = [(link, item) for link in links for item in link['evidence']]
        store = Gtk.ListStore(str, str, str, str, int)
        tree = Gtk.TreeView(model=store)
        for index, name in enumerate(('Direction / kind', 'Symbol / import', 'Use location', 'Definition location')):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
            column = Gtk.TreeViewColumn(name, renderer, text=index)
            column.set_resizable(True)
            column.set_expand(True)
            column.set_max_width(350)
            tree.append_column(column)
        tree.set_tooltip_column(2)
        scroll = Gtk.ScrolledWindow()
        scroll.add(tree)
        content.pack_start(scroll, True, True, 0)
        actions = Gtk.Box(spacing=8, margin=8)
        content.pack_start(actions, False, False, 0)
        more = Gtk.Button(label='Load more')
        count = Gtk.Label()
        cursor = 0

        def location(path, line):
            return f'{path}:{line}' + (' (outside map)' if path not in self.canvas.cards else '')

        def append_page(*_):
            nonlocal cursor
            end = min(cursor + 200, len(rows))
            for index in range(cursor, end):
                link, item = rows[index]
                direction = ('Outgoing' if link['source'] == self.selected else
                             'Incoming' if link['target'] == self.selected else 'Linked')
                store.append([direction + ' / ' + link['kind'], item['symbol'],
                              location(link['source'], item['line']), location(link['target'], item['target_line']), index])
            cursor = end
            count.set_text(f'{cursor} / {len(rows)} source locations')
            more.set_sensitive(cursor < len(rows))
        more.connect('clicked', append_page)
        actions.pack_start(more, False, False, 0)
        actions.pack_start(count, False, False, 0)

        def selected():
            model, iterator = tree.get_selection().get_selected()
            return rows[model[iterator][4]] if iterator is not None else None

        def open_location(target=False):
            row = selected()
            if row and self.open_source:
                link, item = row
                self.open_source(link['target' if target else 'source'],
                                 item['target_line' if target else 'line'], item['target_column' if target else 'column'])

        def focus_symbol(*_):
            row = selected()
            if row and row[1].get('symbol_id'):
                self.references.set_active(True)
                link = row[0]
                selected_path = self.selected if self.selected in (link['source'], link['target']) else link['target']
                self.select(selected_path, row[1]['symbol_id'])
                dialog.destroy()

        def reveal(*_):
            row = selected()
            if row:
                link = row[0]
                path = link['target'] if link['target'] in self.canvas.cards else link['source']
                if path in self.canvas.cards:
                    self.select(path)
                    self.canvas.jump_to_file(path)
        buttons = []
        for label, callback in [('Open use', lambda *_: open_location()), ('Open definition', lambda *_: open_location(True)),
                                ('Focus symbol', focus_symbol), ('Show card', reveal)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', callback)
            actions.pack_start(button, False, False, 0)
            buttons.append(button)

        def selection_changed(*_):
            row = selected()
            buttons[0].set_sensitive(bool(row and self.open_source))
            buttons[1].set_sensitive(bool(row and self.open_source))
            buttons[2].set_sensitive(bool(row and row[1].get('symbol_id')))
            buttons[3].set_sensitive(bool(row and (row[0]['source'] in self.canvas.cards or row[0]['target'] in self.canvas.cards)))
        tree.get_selection().connect('changed', selection_changed)
        tree.connect('row-activated', lambda *_: open_location())
        append_page()
        selection_changed()
        dialog.show_all()
