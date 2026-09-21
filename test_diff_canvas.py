import unittest
from diff_canvas import group, patch_lines, DiffCanvas, Gtk


class CanvasTests(unittest.TestCase):
    def test_long_cards_do_not_overlap_next_column_after_zoom(self):
        import time
        long_patch = '@@ -1 +1 @@\n-' + 'old ' * 120 + '\n+' + 'new ' * 120
        files = [('M', path, path, long_patch) for path in
                 ('pkgs/demo-frontend-web/src/a.ts', 'pkgs/demo-shared/src/b.ts')]
        canvas = DiffCanvas(files)
        window = Gtk.Window()
        window.add(canvas)
        window.show_all()
        try:
            for zoom in (1.0, .5, .25, 1.5):
                canvas.set_zoom(zoom)
                end = time.monotonic() + .15
                while time.monotonic() < end:
                    while Gtk.events_pending(): Gtk.main_iteration()
                    time.sleep(.005)
                cards = [w for w in canvas.board.get_children() if isinstance(w, Gtk.Box)]
                cards.sort(key=lambda w: w.get_allocation().x)
                first, second = [w.get_allocation() for w in cards]
                self.assertLess(first.x + first.width, second.x, f'Overlapping columns at zoom {zoom}')
        finally:
            window.hide()

    def test_viewed_toggle_survives_zoom(self):
        file = ('M', 'a.ts', 'a.ts', '@@ -1 +1 @@\n-old\n+new')
        canvas = DiffCanvas([file])
        def checkbox():
            card = next(w for w in canvas.board.get_children() if isinstance(w, Gtk.Box))
            return next(w for w in card.get_children()[0].get_children() if isinstance(w, Gtk.CheckButton))
        def diff_count():
            return sum(isinstance(child, Gtk.TextView)
                       for card in canvas.board.get_children() if isinstance(card, Gtk.Box)
                       for child in card.get_children())
        self.assertEqual(diff_count(), 1)
        checkbox().set_active(True)
        self.assertEqual(diff_count(), 0)
        self.assertTrue(canvas.progress.is_viewed(file))
        self.assertEqual(canvas.read_count.get_text(), '✓ 1 / 1 viewed')
        canvas.set_zoom(.5)
        self.assertTrue(checkbox().get_active())
        self.assertEqual(diff_count(), 0)
        checkbox().set_active(False)
        self.assertEqual(diff_count(), 1)
        self.assertEqual(canvas.read_count.get_text(), '✓ 0 / 1 viewed')
        canvas.destroy()

    def test_sticky_path_follows_long_file_and_next_file(self):
        patch = '@@ -0,0 +1,100 @@\n' + '\n'.join('+line' for _ in range(100))
        paths = ['pkgs/demo-shared/src/first.ts', 'pkgs/demo-shared/src/next.ts']
        canvas = DiffCanvas([('A', p, p, patch) for p in paths])
        window = Gtk.Window(); window.set_default_size(900, 600)
        window.add(canvas); window.show_all()
        while Gtk.events_pending(): Gtk.main_iteration()
        for index, path in enumerate(paths):
            file, x, y, width, height = canvas.card_positions[index]
            canvas.scroll.get_hadjustment().set_value(x)
            canvas.scroll.get_vadjustment().set_value(y + height / 2)
            canvas.update_location()
            self.assertEqual(canvas.active_file[2], path)
            self.assertEqual(canvas.path_label.get_text(), 'pkgs › demo-shared › src')
            self.assertEqual(canvas.file_label.get_text(), path.split('/')[-1])
            for card_path, card in canvas.cards.items():
                self.assertEqual(card.get_style_context().has_class('focused-card'), card_path == path)
            active = canvas.cards[path]
            size = active.get_preferred_size()[1]
            active.get_style_context().remove_class('focused-card')
            unfocused_size = active.get_preferred_size()[1]
            self.assertEqual((size.width, size.height), (unfocused_size.width, unfocused_size.height))
            active.get_style_context().add_class('focused-card')
        canvas.sticky_viewed.set_active(True)
        self.assertTrue(canvas.progress.is_viewed(('A', paths[1], paths[1], patch)))
        window.hide()

    def test_wheel_burst_redraws_once_after_pause(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from diff_canvas import Gdk, GLib
        import time
        canvas = DiffCanvas([])
        event = SimpleNamespace(state=Gdk.ModifierType.CONTROL_MASK, direction=Gdk.ScrollDirection.UP)
        with patch.object(canvas, 'render') as render:
            for _ in range(8):
                canvas.wheel(canvas.board, event)
            render.assert_not_called()
            self.assertAlmostEqual(canvas.pending_zoom, 1.08 ** 8)
            end = time.monotonic() + .3
            while time.monotonic() < end:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                time.sleep(.005)
            render.assert_called_once()
            self.assertAlmostEqual(canvas.zoom, 1.08 ** 8)
        canvas.queue_zoom(.5)
        canvas.destroy()
        self.assertIsNone(canvas.zoom_timer)

    def test_groups(self):
        self.assertEqual(group('pkgs/document-backend-shared/src/a.ts'), 'Backend')
        self.assertEqual(group('pkgs/document-shared/src/a.ts'), 'Shared/API')
        self.assertEqual(group('docs/file.md'), 'Other')

    def test_hunks_and_colors(self):
        lines = patch_lines('diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -2,2 +2,2 @@\n-old\n+new\n same')
        self.assertEqual([kind for kind, _ in lines], ['hunk','del','add','context'])
        self.assertIn('2', lines[1][1])
        self.assertNotIn('--- a/a', str(lines))

    def test_folder_nodes_and_open_cards(self):
        patch='@@ -1 +1 @@\n-before\n+after'
        files=[('M','pkgs/demo-shared/src/a.ts','pkgs/demo-shared/src/a.ts',patch),
               ('A','pkgs/demo-shared/src/b.ts','pkgs/demo-shared/src/b.ts',patch)]
        canvas=DiffCanvas(files)
        window=Gtk.Window(); window.set_default_size(900,600); window.add(canvas); window.show_all()
        while Gtk.events_pending(): Gtk.main_iteration()
        labels=[w.get_text() for w in canvas.board.get_children() if isinstance(w,Gtk.Label)]
        self.assertEqual(labels.count('pkgs'),1)
        self.assertEqual(labels.count('src'),1)
        def descendants(widget):
            yield widget
            if isinstance(widget,Gtk.Container):
                for child in widget.get_children(): yield from descendants(child)
        children=list(descendants(canvas.board))
        self.assertEqual(sum(isinstance(w,Gtk.TextView) for w in children),2)
        self.assertFalse(any(isinstance(w,(Gtk.Expander,Gtk.ScrolledWindow)) for w in children))
        canvas.set_zoom(.5)
        self.assertEqual(canvas.zoom,.5)
        self.assertEqual(len(canvas.files),2)
        window.hide()


if __name__=='__main__': unittest.main()
