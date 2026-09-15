#!/usr/bin/python3
"""Read-only prototype. Graph order is source order, not execution proof."""
import json
import os
from flow_model import render, FlowError
import math
import cairo
import sqlite3
import subprocess
from pathlib import Path
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

ROOT = Path(os.environ.get('CODE_FLOW_ROOT', '/home/julian/plancraft'))
meta = json.loads((ROOT / '.tokensave/branch-meta.json').read_text())
con = sqlite3.connect(f"file:{ROOT / '.tokensave' / meta['branches']['main']['db_file']}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
W, H, DX, DY = 370, 180, 425, 260

def lookup(name, suffix=''):
    return con.execute('select * from nodes where name=? and file_path like ? order by start_line limit 1', (name, '%' + suffix)).fetchone()

class Window(Gtk.Window):
    def __init__(self):
        super().__init__(title='Code Flow — Prototype')
        self.set_default_size(1320, 820)
        self.connect('destroy', Gtk.main_quit)
        self.flow_path = Path(__file__).with_name('flows') / 'publish-request.compact.json'
        self.expanded = set()
        self.zoom = 1.0
        self.pan_origin = None
        self.connect('key-press-event', self.on_key)
        self.nodes, self.edges, self.labels = [], [], []
        self.roots = []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        self.add(box)
        bar = Gtk.Box(spacing=8)
        self.query = Gtk.SearchEntry(placeholder_text='Function name…')
        self.query.connect('activate', self.search)
        bar.pack_start(self.query, True, True, 0)
        for label, fn in [('Find function', self.search), ('Publish example', self.example), ('Open flow JSON', self.open_flow), ('Collapse all', self.collapse)]:
            b = Gtk.Button(label=label)
            b.connect('clicked', fn)
            bar.pack_start(b, False, False, 0)
        box.pack_start(bar, False, False, 0)
        box.pack_start(Gtk.Label(label='→ Continue at the same depth     ↓ Drill into a function     ⇡ Dashed: return to its caller', xalign=0), False, False, 0)
        self.notice = Gtk.Label(xalign=0)
        self.notice.set_line_wrap(True)
        box.pack_start(self.notice, False, False, 0)
        self.canvas = Gtk.EventBox()
        self.image = Gtk.Image()
        self.canvas.add(self.image)
        self.canvas.set_can_focus(True)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.canvas.connect('button-release-event', self.pan_end)
        self.canvas.connect('motion-notify-event', self.pan_move)
        self.canvas.connect('scroll-event', self.on_scroll)
        self.canvas.connect('button-press-event', self.click)
        scroll = Gtk.ScrolledWindow()
        self.scroll = scroll
        scroll.add_with_viewport(self.canvas)
        box.pack_start(scroll, True, True, 0)
        box.pack_start(Gtk.Label(label='Click a card to expand/collapse. Click its blue filename to open Zed. Scroll horizontally for long paths. No files are changed.', xalign=0), False, False, 0)
        self.example()

    def collapse(self, *_):
        self.expanded.clear()
        self.layout()

    def search(self, *_):
        q = self.query.get_text().strip()
        if not q:
            return
        rows = con.execute("select * from nodes where name like ? and kind in ('function','arrow_function','method','const') order by (name=?) desc, name, file_path limit 60", ('%' + q + '%', q)).fetchall()
        dialog = Gtk.Dialog(title='Choose a function', transient_for=self, modal=True)
        dialog.set_default_size(950, 450)
        dialog.add_button('Cancel', Gtk.ResponseType.CANCEL)
        store = Gtk.ListStore(str, str, int)
        for i, row in enumerate(rows):
            store.append([row['name'], f"{row['file_path']}:{row['start_line']}", i])
        tree = Gtk.TreeView(model=store)
        for i, name in enumerate(['Function', 'Source']):
            tree.append_column(Gtk.TreeViewColumn(name, Gtk.CellRendererText(), text=i))
        tree.connect('row-activated', lambda *_: dialog.response(Gtk.ResponseType.OK))
        sc = Gtk.ScrolledWindow()
        sc.add(tree)
        dialog.get_content_area().pack_start(sc, True, True, 0)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            model, it = tree.get_selection().get_selected()
            if it is not None:
                selected = rows[model[it][2]]
                self.verified = selected['name'] == 'publishDocumentRequest' and selected['file_path'].endswith('api-functions/documents/publishDocumentRequest.ts')
                self.roots = [('Selected function · no verified execution flow', selected)]
                self.expanded = {('0',)}
                self.notice.set_text('INDEXED VIEW · Calls are ordered by source line. Conditions, await/async behavior and actual returns are not resolved. Dashed arrows illustrate return-to-caller, not proven runtime behavior.')
                self.layout()
        dialog.destroy()

    def open_flow(self, *_):
        dialog = Gtk.FileChooserDialog(title='Open a flow JSON', transient_for=self, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Open', Gtk.ResponseType.OK)
        dialog.set_current_folder(str(Path(__file__).with_name('flows')))
        if dialog.run() == Gtk.ResponseType.OK:
            self.flow_path = Path(dialog.get_filename())
            self.verified = True
            self.layout()
        dialog.destroy()

    def example(self, *_):
        self.flow_path = Path(os.environ.get('CODE_FLOW_FILE', str(Path(__file__).with_name('flows') / 'publish-request.compact.json')))
        self.verified = True
        self.layout()

    def old_example(self, *_):
        self.roots = [(label, lookup(name, suffix)) for label, name, suffix in [
            ('Browser entry — click Publish', 'onPublishDocumentFlow', 'useEditorInvoicePublishFlow.tsx'),
            ('API entry — HTTP handoff (separate runtime)', 'publishDocumentRequest', 'publishDocumentRequest.ts'),
            ('Background entry — queued job (separate runtime)', 'runPublishDocumentJob', 'runPublishDocumentJob.ts')]]
        self.expanded = {('1',)}
        self.notice.set_text('PUBLISH EXAMPLE · Separate runtime entry points, not nested calls. Expansions use tokensave source order, NOT a verified execution trace. Return arrows show the proposed visual convention.')
        self.layout()

    def card(self, x, y, title, row=None, key=None, hint=''):
        item = dict(x=x, y=y, title=title, row=row, key=key, hint=hint)
        self.nodes.append(item)
        return item

    def calls(self, row):
        return con.execute("select n.*, min(e.line) as call_line from edges e join nodes n on n.id=e.target where e.source=? and e.kind='calls' group by n.id order by call_line, n.name limit 16", (row['id'],)).fetchall()

    def branch(self, row, key, x, y, ancestors):
        root = self.card(x, y, row['name'], row, key, '− Collapse' if key in self.expanded else '+ Drill down')
        root['hint'] = 'Source only · execution flow not verified'
        if True:
            return root, root, x + W, y + H
        calls = self.calls(row)
        if not calls:
            root['hint'] = 'No indexed calls'
            return root, root, x + W, y + H
        self.labels.append((x, y + DY - 20, 'INSIDE ' + row['name'] + ' · indexed calls / source order'))
        cursor, bottom, prev = x, y + H, None
        first = None
        for i, child in enumerate(calls):
            start, end, right, low = self.branch(child, key + (str(i),), cursor, y + DY, ancestors | {row['id']})
            if first is None:
                first = start
            if prev:
                self.edges.append((prev, start, 'sequence'))
            prev = end
            cursor = right + 55
            bottom = max(bottom, low)
        # An explicit continuation belongs at the caller depth, not among its children.
        resume = self.card(cursor, y, 'Back in caller', hint='Continue after this call · schematic')
        self.edges.append((root, first, 'call'))
        self.edges.append((prev, resume, 'return'))
        return root, resume, cursor + W, bottom

    def layout(self):
        self.bands = []
        self.nodes, self.edges, self.labels = [], [], []
        y, right = 60, 1100
        for i, (label, row) in enumerate(self.roots):
            self.labels.append((30, y - 22, label))
            if row:
                _, _, r, bottom = self.branch(row, (str(i),), 30, y, set())
                right = max(right, r + 50)
                y = bottom + 110
            else:
                self.card(30, y, 'Entry missing from index')
                y += DY
        if getattr(self, 'verified', False):
            self.nodes, self.edges, self.labels = [], [], []
            try:
                right, y = render(self, self.flow_path, ROOT, W, H)
            except (FlowError, ValueError, KeyError, TypeError, OSError) as error:
                self.nodes, self.edges, self.labels = [], [], []
                self.notice.set_text('Cannot display flow: ' + str(error))
                right, y = 1100, 500
        width, height = min(int(right), 30000), min(int(y), 10000)
        self.canvas.set_size_request(width, height)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        self.draw(None, cairo.Context(surface))
        import io
        data = io.BytesIO()
        surface.write_to_png(data)
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.write(data.getvalue())
        loader.close()
        self.original_pixbuf = loader.get_pixbuf()
        self.zoom = 1.0
        self.apply_zoom()
        GLib.timeout_add(100, self.focus_start)

    def focus_start(self):
        if not self.nodes:
            return False
        first = min(self.nodes, key=lambda n: (n['x'], n['y']))
        for adjustment, value in [(self.scroll.get_hadjustment(), first['x'] * self.zoom - 30),
                                  (self.scroll.get_vadjustment(), first['y'] * self.zoom - 65)]:
            adjustment.set_value(max(adjustment.get_lower(), min(adjustment.get_upper() - adjustment.get_page_size(), value)))
        self.canvas.grab_focus()
        return False

    def text(self, cr, x, y, text, size=13, color=(.82,.85,.91)):
        cr.set_source_rgb(*color)
        cr.set_font_size(size)
        cr.move_to(x, y)
        cr.show_text(text)

    def draw(self, widget, cr):
        cr.set_source_rgb(.075,.09,.12)
        cr.paint()
        for index, (top, bottom, right, label) in enumerate(sorted(self.bands)):
            colors = [(.105,.14,.19), (.13,.12,.18), (.10,.16,.15)]
            cr.set_source_rgb(*colors[index % len(colors)])
            cr.rectangle(8, top, right-16, bottom-top)
            cr.fill()
            cr.set_source_rgb(.32,.39,.47)
            cr.set_line_width(1)
            cr.move_to(8, top); cr.line_to(right-8, top)
            cr.move_to(8, bottom); cr.line_to(right-8, bottom)
            cr.stroke()
        for a, b, kind in self.edges:
            if kind == 'sequence':
                ax, ay = a['x']+W, a['y']+H/2
                bx, by = b['x'], b['y']+H/2
                middle = ax + 24
                points = [(ax, ay), (middle, ay), (middle, by), (bx, by)]
            elif kind == 'call':
                points = [(a['x']+W/2, a['y']+H), (b['x']+W/2, b['y'])]
            else:
                points = [(a['x']+W, a['y']+H/2), (b['x']+W/2, a['y']+H/2), (b['x']+W/2, b['y']+H)]
            cr.set_source_rgb(*((.58,.74,.98) if kind != 'return' else (.66,.8,.56)))
            cr.set_line_width(2)
            cr.set_dash([7,5] if kind == 'return' else [])
            cr.move_to(*points[0])
            for point in points[1:]: cr.line_to(*point)
            cr.stroke()
            cr.set_dash([])
            x,y=points[-1]; px,py=points[-2]
            angle=math.atan2(y-py,x-px)
            cr.move_to(x,y)
            cr.line_to(x-10*math.cos(angle-.45),y-10*math.sin(angle-.45))
            cr.line_to(x-10*math.cos(angle+.45),y-10*math.sin(angle+.45))
            cr.close_path(); cr.fill()
        for x,y,label in self.labels:
            self.text(cr,x,y,label,13)
        for n in self.nodes:
            x,y=n['x'],n['y']
            kind = n.get('kind', 'call')
            palette = {
                'condition': ((.25,.20,.10), (.98,.73,.27), 'IF · DECISION'),
                'call': ((.12,.18,.27), (.42,.68,1), 'CALL · FUNCTION'),
                'throw': ((.29,.12,.16), (1,.46,.5), 'THROW · STOP'),
                'return': ((.10,.23,.18), (.39,.85,.62), 'RETURN · DONE'),
            }
            bg, accent, label = palette[kind]
            cr.set_source_rgb(*bg)
            cr.rectangle(x,y,W,H); cr.fill()
            cr.set_source_rgb(*accent)
            cr.set_line_width(2)
            cr.rectangle(x+1,y+1,W-2,H-2); cr.stroke()
            if kind == 'condition':
                cr.move_to(x+20,y+9); cr.line_to(x+28,y+17)
                cr.line_to(x+20,y+25); cr.line_to(x+12,y+17)
                cr.close_path(); cr.stroke()
            else:
                cr.arc(x+20,y+17,5,0,2*math.pi); cr.fill()
            self.text(cr,x+36,y+21,label,10,accent)
            self.text(cr,x+12,y+46,n['title'][:43],13)
            if n['row']:
                file=str(ROOT / n['row']['file_path']) + ':' + str(n['row']['start_line'])
                cr.set_font_size(11)
                lines, line = [], ''
                for char in file:
                    if line and cr.text_extents(line + char).width > W - 24:
                        lines.append(line)
                        line = ''
                    line += char
                if line:
                    lines.append(line)
                for index, line in enumerate(lines):
                    self.text(cr,x+12,y+70+index*15,line,11,(.48,.74,1))
            self.text(cr,x+12,y+H-15,n['hint'],11,(.64,.7,.78))
        return False

    def apply_zoom(self):
        source = self.original_pixbuf
        width = max(1, round(source.get_width() * self.zoom))
        height = max(1, round(source.get_height() * self.zoom))
        self.image.set_from_pixbuf(source.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR))
        self.image.set_alignment(0, 0)
        self.canvas.set_size_request(width, height)
        self.set_title(f'Code Flow — Prototype · {round(self.zoom * 100)}%')

    def on_scroll(self, widget, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        if event.direction == Gdk.ScrollDirection.UP:
            delta = -1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta = 1
        else:
            _, _, delta = event.get_scroll_deltas()
        if delta:
            self.zoom = max(0.2, min(2.0, self.zoom * (1.15 if delta < 0 else 1 / 1.15)))
            self.apply_zoom()
        return True

    def shift_view(self, dx, dy):
        for adjustment, delta in [(self.scroll.get_hadjustment(), dx), (self.scroll.get_vadjustment(), dy)]:
            adjustment.set_value(max(adjustment.get_lower(), min(adjustment.get_upper() - adjustment.get_page_size(), adjustment.get_value() + delta)))

    def on_key(self, widget, event):
        if isinstance(self.get_focus(), Gtk.Entry):
            return False
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            return False
        moves = {'w': (0, -70), 'a': (-70, 0), 's': (0, 70), 'd': (70, 0)}
        key = (Gdk.keyval_name(event.keyval) or '').lower()
        if key not in moves:
            return False
        self.shift_view(*moves[key])
        return True

    def pan_move(self, widget, event):
        if self.pan_origin is None:
            return False
        x, y = self.pan_origin
        self.shift_view(x - event.x_root, y - event.y_root)
        self.pan_origin = (event.x_root, event.y_root)
        return True

    def pan_end(self, widget, event):
        if event.button != 2:
            return False
        self.pan_origin = None
        widget.get_window().set_cursor(None)
        return True

    def click(self, widget, event):
        self.canvas.grab_focus()
        if event.button == 2:
            self.pan_origin = (event.x_root, event.y_root)
            widget.get_window().set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), 'grabbing'))
            return True
        if event.button != 1:
            return False
        x, y = event.x / self.zoom, event.y / self.zoom
        for n in reversed(self.nodes):
            if n['x'] <= x <= n['x']+W and n['y'] <= y <= n['y']+H:
                if n['row'] is None: return
                if 55 <= y-n['y'] <= H-35:
                    p=(ROOT / n['row']['file_path']).resolve()
                    if p.is_relative_to(ROOT) and p.is_file():
                        subprocess.Popen(['zed', str(p)+':'+str(n['row']['start_line'])])
                elif n['key'] is None:
                    return True
                elif n['key'] in self.expanded:
                    self.expanded.remove(n['key']); self.layout()
                else:
                    self.expanded.add(n['key']); self.layout()
                return

if __name__ == '__main__':
    w=Window(); w.show_all(); Gtk.main()
