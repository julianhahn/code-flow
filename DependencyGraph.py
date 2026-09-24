"""Static dependency links and focus rules; no GTK or filesystem access."""
from collections import defaultdict
from math import hypot


class DependencyGraph:
    KINDS = frozenset(('import', 'reference'))

    def __init__(self, data=None):
        self.data = data or {}
        self.edges = self.data.get('edges', [])
        self.by_file = defaultdict(list)
        self.by_symbol = defaultdict(list)
        for edge in self.edges:
            self.by_file[edge['source']].append(edge)
            if edge['target'] != edge['source']:
                self.by_file[edge['target']].append(edge)
            for symbol in {item.get('symbol_id') for item in edge['evidence']} - {None}:
                self.by_symbol[symbol].append(edge)

    def links(self, kinds, selected=None, symbol=None, show_all=False):
        if symbol and 'reference' in kinds:
            candidates = self.by_symbol.get(symbol, ())
        elif selected is not None:
            candidates = self.by_file.get(selected, ())
        elif show_all:
            candidates = self.edges
        else:
            return []
        result = []
        for edge in candidates:
            if edge['kind'] not in kinds:
                continue
            if symbol:
                evidence = [e for e in edge['evidence'] if e.get('symbol_id') == symbol]
                if not evidence:
                    continue
                edge = {**edge, 'evidence': evidence}
            result.append(edge)
        return result

    def neighbours(self, selected, kinds, symbol=None):
        if selected is None:
            return set()
        paths = {selected}
        for edge in self.links(kinds, selected, symbol):
            paths.update((edge['source'], edge['target']))
        return paths

    @staticmethod
    def route(source, target, source_rail, target_rail, lane, zoom):
        """Right-hand card ports and column gutters keep lines out of diff text."""
        sx, sy, sw, sh = source
        tx, ty, tw, th = target
        start = (sx + sw, sy + min(sh / 2, 18 * zoom))
        end = (tx + tw, ty + min(th / 2, 18 * zoom))
        offset = (14 + lane % 6 * 4) * zoom
        sr, tr = source_rail + offset, target_rail + offset
        if source_rail == target_rail:
            return [start, (sr, start[1]), (sr, end[1]), end]
        top = (48 + lane % 6 * 5) * zoom
        return [start, (sr, start[1]), (sr, top), (tr, top), (tr, end[1]), end]

    @staticmethod
    def distance(point, route):
        """Distance to the same polyline that is drawn, for arrow hit testing."""
        px, py = point
        best = float('inf')
        for (ax, ay), (bx, by) in zip(route, route[1:]):
            dx, dy = bx - ax, by - ay
            length = dx * dx + dy * dy
            t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / length)) if length else 0
            best = min(best, hypot(px - ax - t * dx, py - ay - t * dy))
        return best
