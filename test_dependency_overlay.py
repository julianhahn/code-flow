"""Selection, routing, evidence, and worker lifetime regression tests."""
import copy
import math
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from DependencyGraph import DependencyGraph


def edge(source, target, kind='reference', symbol='run'):
    return dict(source=source, target=target, kind=kind, evidence=[dict(
        symbol=symbol, symbol_id=f'{target}:10' if kind == 'reference' else None,
        line=3, column=5, target_line=1, target_column=8)])


A = 'pkgs/demo-frontend/src/a.ts'
B = 'pkgs/demo-shared/src/b.ts'
C = 'pkgs/demo-backend/src/c.ts'
D = 'pkgs/demo-backend/src/d.ts'
OUTSIDE = 'pkgs/demo-backend/src/unchanged.ts'


def data():
    return dict(typescript='test', edges=[edge(A, B, 'import'), edge(A, B), edge(C, A), edge(OUTSIDE, B)],
                coverage=dict(complete=True, files=5, analysedFiles=[A, B, C, D, OUTSIDE], issues=[]))


class GraphTests(unittest.TestCase):
    def test_direct_both_directions_only_and_separate_toggles(self):
        graph = DependencyGraph(data())
        self.assertEqual(graph.neighbours(A, {'reference'}), {A, B, C})
        self.assertEqual(graph.neighbours(A, {'import'}), {A, B})
        self.assertEqual(graph.neighbours(A, set()), {A})
        self.assertNotIn(OUTSIDE, graph.neighbours(A, {'reference'}))
        self.assertEqual(graph.links({'reference'}), [])
        self.assertEqual(len(graph.links({'reference'}, show_all=True)), 3)

    def test_symbol_focus_includes_unchanged_callers_without_indirect_links(self):
        graph = DependencyGraph(data())
        self.assertEqual(graph.neighbours(A, {'reference', 'import'}, f'{B}:10'), {A, B, OUTSIDE})
        self.assertEqual(len(graph.links({'reference'}, A, f'{B}:10')), 2)
        self.assertEqual(graph.links({'import'}, A, f'{B}:10'), [])

    def test_routes_hit_detection_and_zoom_do_not_move_cards(self):
        for zoom in (.25, 1, 2):
            source = tuple(v * zoom for v in (100, 300, 500, 120))
            target = tuple(v * zoom for v in (800, 900, 600, 200))
            route = DependencyGraph.route(source, target, 600 * zoom, 1400 * zoom, 0, zoom)
            self.assertEqual(route[0][0], source[0] + source[2])
            self.assertEqual(route[-1][0], target[0] + target[2])
            for left, right in zip(route, route[1:]):
                self.assertTrue(left[0] == right[0] or left[1] == right[1])
                midpoint = ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
                self.assertEqual(DependencyGraph.distance(midpoint, route), 0)
            self.assertGreater(DependencyGraph.distance((0, 0), route), 0)


class OverlayTests(unittest.TestCase):
    def setUp(self):
        from diff_canvas import DiffCanvas, Gtk
        self.Gtk = Gtk
        patch_text = '@@ -1 +1 @@\n-old\n+new'
        self.canvas = DiffCanvas([('M', p, p, patch_text) for p in (A, B, C, D)], open_source=Mock())
        self.addCleanup(self.canvas.destroy)
        self.overlay = self.canvas.dependencies
        self.overlay.accept(data())

    def test_disabled_by_default_focus_fades_only_unrelated_cards_and_folders(self):
        overlay = self.overlay
        original = list(self.canvas.card_positions)
        self.assertEqual(overlay.kinds, set())
        self.assertEqual(overlay.routes, [])
        overlay.references.set_active(True)
        overlay.select(A)
        for path, card in self.canvas.cards.items():
            self.assertAlmostEqual(card.get_opacity(), 1 if path != D else .35, delta=.005)
            self.assertTrue(card.get_sensitive())
            self.assertEqual(card.get_style_context().has_class('dependency-selected'), path == A)
        backend_branch = [(widget, prefix) for widget, column, prefix in self.canvas.folder_widgets if column == 'Backend']
        self.assertTrue(backend_branch)
        for widget, prefix in backend_branch:
            self.assertAlmostEqual(widget.get_opacity(), .35 if prefix == D else 1, delta=.005)
        self.assertEqual(self.canvas.card_positions, original)
        overlay.select(None)
        self.assertEqual(overlay.routes, [])
        self.assertTrue(all(card.get_opacity() == 1 for card in self.canvas.cards.values()))

    def test_toggling_off_and_zoom_restore_focus_without_changing_order(self):
        overlay = self.overlay
        overlay.references.set_active(True)
        overlay.select(A)
        order = [file[2] for file, *_ in self.canvas.card_positions]
        for zoom in (.25, 1.5):
            self.canvas.set_zoom(zoom)
            self.assertEqual([file[2] for file, *_ in self.canvas.card_positions], order)
            self.assertEqual(len(overlay.routes), 2)
            self.assertAlmostEqual(self.canvas.cards[D].get_opacity(), .35, delta=.005)
        overlay.references.set_active(False)
        self.assertEqual(overlay.routes, [])
        self.assertTrue(all(card.get_opacity() == 1 for card in self.canvas.cards.values()))
        overlay.imports.set_active(True)
        self.assertEqual(len(overlay.routes), 1)
        self.assertAlmostEqual(self.canvas.cards[C].get_opacity(), .35, delta=.005)

    def test_clicking_text_keeps_chat_selection_and_background_clears_focus(self):
        text = next(child for child in self.canvas.cards[A].get_children() if isinstance(child, self.Gtk.TextView))
        self.overlay.references.set_active(True)
        event = SimpleNamespace(button=1, x_root=0, y_root=0)
        self.assertFalse(self.canvas.press(text, event))
        self.assertEqual(self.overlay.selected, A)
        buffer = text.get_buffer()
        buffer.select_range(buffer.get_start_iter(), buffer.get_end_iter())
        self.assertEqual(self.canvas.selected_context['file'][2], A)
        self.canvas.press(self.canvas.board, event)
        self.assertIsNone(self.overlay.selected)
        self.assertIsNotNone(self.canvas.selected_context)
        title = self.canvas.cards[A].get_children()[0].get_children()[0]
        self.assertTrue(self.canvas.press(title, event))
        self.assertEqual(self.overlay.selected, A)

    def test_evidence_lists_outside_files_and_can_focus_symbol(self):
        overlay = self.overlay
        overlay.references.set_active(True)
        overlay.select(B)
        overlay.show_evidence()
        dialog = overlay.dialogs[-1]
        def descendants(widget):
            yield widget
            if isinstance(widget, self.Gtk.Container):
                for child in widget.get_children():
                    yield from descendants(child)
        widgets = list(descendants(dialog))
        tree = next(widget for widget in widgets if isinstance(widget, self.Gtk.TreeView))
        store = tree.get_model()
        self.assertEqual(len(store), 2)
        self.assertTrue(any('outside map' in row[2] for row in store))
        tree.get_selection().select_path(0)
        next(widget for widget in widgets if isinstance(widget, self.Gtk.Button) and widget.get_label() == 'Open definition').clicked()
        overlay.open_source.assert_called_once_with(B, 1, 8)
        next(widget for widget in widgets if isinstance(widget, self.Gtk.Button) and widget.get_label() == 'Focus symbol').clicked()
        self.assertEqual(overlay.symbol, f'{B}:10')
        self.assertEqual(overlay.dialogs, [])
        self.assertEqual(len(overlay.visible_links()), 2)

    def test_draw_and_hit_follow_scroll_and_zoom(self):
        import cairo
        overlay = self.overlay
        overlay.imports.set_active(True)
        overlay.references.set_active(True)
        overlay.select(A)
        # Repeated references exercise the count-label drawing path.
        overlay.graph.edges[1]['evidence'].append(copy.deepcopy(overlay.graph.edges[1]['evidence'][0]))
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1200, 800)
        for zoom in (1, .3):
            self.canvas.set_zoom(zoom)
            cr = cairo.Context(surface)
            self.assertFalse(overlay.draw(overlay.layer, cr))
            edge_value, route = overlay.routes[0]
            x, y = route[1]
            with patch.object(overlay.layer, 'get_realized', return_value=True), \
                    patch.object(overlay.layer, 'get_window', return_value=SimpleNamespace(get_origin=lambda: (1, 50, 75))):
                event = SimpleNamespace(x_root=x + 50 - self.canvas.scroll.get_hadjustment().get_value(),
                                        y_root=y + 75 - self.canvas.scroll.get_vadjustment().get_value())
                self.assertIsNotNone(overlay.hit(event))

    def test_show_all_keeps_unrelated_arrows_faint_when_a_file_is_selected(self):
        import cairo
        graph = data()
        graph['edges'].append(edge(C, D))
        self.overlay.accept(graph)
        self.overlay.references.set_active(True)
        self.overlay.show_all.set_active(True)
        self.overlay.select(A)
        self.assertEqual(len(self.overlay.routes), 3)
        self.assertNotIn((C, D, 'reference'), self.overlay.active_edges)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1200, 800)
        cr = Mock(wraps=cairo.Context(surface))
        self.overlay.draw(self.overlay.layer, cr)
        cr.set_source_rgba.assert_any_call(.53, .18, .72, .94 * .18)

    def test_deleted_and_uncovered_files_are_not_reported_as_having_no_references(self):
        self.overlay.references.set_active(True)
        self.overlay.select('deleted.ts')
        self.canvas.files.append(('D', 'deleted.ts', 'deleted.ts', ''))
        self.overlay.update()
        self.assertIn('Deleted: no head-version links', self.overlay.status.get_text())
        self.overlay.select('not-covered.ts')
        self.assertIn('outside analysed coverage', self.overlay.status.get_text())

    def test_reset_clears_focus_and_closes_evidence(self):
        self.overlay.references.set_active(True)
        self.overlay.select(A)
        self.overlay.show_evidence()
        self.assertTrue(self.overlay.dialogs)
        self.overlay.reset()
        self.assertFalse(self.overlay.loaded)
        self.assertIsNone(self.overlay.selected)
        self.assertEqual(self.overlay.graph.edges, [])
        self.assertEqual(self.overlay.dialogs, [])

    def test_multiline_failure_cannot_push_the_map_out_of_view(self):
        trace = 'Crash\n' + '123: native stack frame\n' * 300
        self.overlay.unavailable(trace)
        self.assertNotIn('\n', self.overlay.status.get_text())
        self.assertLessEqual(len(self.overlay.status.get_text()), 600)
        self.assertTrue(self.overlay.status.get_single_line_mode())
        self.assertIn(trace, self.overlay.status.get_tooltip_text())

    def test_overlay_has_no_install_or_analyse_buttons(self):
        bar = self.overlay.controls.get_children()[0]
        labels = [button.get_label() for button in bar.get_children()]
        self.assertEqual(labels, ['Imports', 'References', 'Show all links', 'Connections (0)', 'Clear focus'])
        self.overlay.reset()
        with patch('DependencyAnalysis.DependencyAnalysis.run') as run:
            self.overlay.references.set_active(True)
            run.assert_not_called()
        self.overlay.unavailable('Compiler could not load')
        self.assertIn('Reopen the diff map to retry', self.overlay.status.get_text())


if __name__ == '__main__':
    unittest.main()
