"""Check keyboard navigation using desktop events, accessible labels and painted borders."""
from pathlib import Path
import re
import sys
import tempfile
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib
from PIL import Image, ImageChops

from scenarios import MAIN, RESULTS, click, exercise, pixels, window_id, xdo
from pr_28118 import LANES, TITLE, prepare_pr_28118

OUTPUT = RESULTS / "navigation"
ACCENT = (88, 166, 255)


def until(description, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        value = predicate()
        if value:
            return value
        time.sleep(.04)
    raise AssertionError(description)


def children(node):
    return (node.get_child_at_index(i) for i in range(node.get_child_count()))


def find(root, role, name):
    """Find the named visible desktop control without activating or focusing it."""
    queue = [root]
    while queue:
        queue, parents = [], queue
        for node in parents:
            if (node.get_role_name() == role and node.get_name() == name and
                    node.get_state_set().contains(Atspi.StateType.SHOWING)):
                return node
            queue.extend(child for child in children(node) if child)


def box(node):
    r = node.get_extents(Atspi.CoordType.SCREEN)
    return r.x, r.y, r.x + r.width, r.y + r.height


def midpoint(node):
    x, y, r, b = box(node)
    return (x + r) // 2, (y + b) // 2


class Map:
    def __init__(self, window):
        self.window = window
        location = find(window, "push button", "Open in Zed").get_parent()
        labels = location.get_child_at_index(0)
        self.breadcrumb, self.filename = children(labels)
        bar = find(window, "push button", "Fit all").get_parent()
        self.zoom_label = bar.get_child_at_index(5)
        self.count_label = bar.get_child_at_index(6)
        self.sticky = location.get_child_at_index(1)
        self.load_cards()
        assert set(self.cards) == {p for lane in LANES.values() for p in lane}, "Fixture and rendered files differ"

    def load_cards(self):
        def collect():
            # GTK replaces the layout after animated zoom. AT-SPI can return
            # an old object until its removal event reaches this client.
            try:
                lane = find(self.window, "label", "FRONTEND")
                if not lane:
                    return False
                board = lane.get_parent()
                cards, titles = {}, {}
                for card in children(board):
                    if not card or card.get_role_name() != "filler":
                        continue
                    header = card.get_child_at_index(0)
                    title_box = header and header.get_child_at_index(0)
                    title = title_box and title_box.get_child_at_index(0)
                    if not title:
                        return False
                    path = title.get_description()
                    cards[path], titles[path] = card, title
                count = re.search(r"/ (\d+) viewed", self.count_label.get_name())
                if not count or len(cards) != int(count[1]):
                    return False
                self.board, self.cards, self.titles = board, cards, titles
                return True
            except GLib.Error:
                return False
        until("Incomplete rendered file list", collect, timeout=8)

    def selected(self):
        return self.breadcrumb.get_description(), self.filename.get_name()

    def zoom(self):
        return int(self.zoom_label.get_name().split("%", 1)[0])

    def visible_point(self, control):
        l, t, r, b = box(self.board)
        for _ in range(60):
            x, y = midpoint(control)
            if not l <= x < r:
                key = "Left" if x < l else "Right"
            elif not t <= y < b:
                key = "Up" if y < t else "Down"
            else:
                return x, y
            xdo("key", "shift+" + key)
            until("Shift+arrow did not move the viewport", lambda: midpoint(control) != (x, y))
        raise AssertionError("Cannot bring the requested control onto the screen")

    def select_title(self, path, double=False):
        x, y = self.visible_point(self.titles[path])
        if double:
            xdo("mousemove", x, y, "click", "--repeat", 2, "--delay", 75, 1)
        else:
            click(x, y)

    def fit(self):
        click(*midpoint(find(self.window, "push button", "Fit all")))
        until("Fit all did not zoom out", lambda: self.zoom() < 100)
        self.load_cards()

    def assert_selection(self, step, path, centered=True):
        try:
            until("selection did not reach the requested file",
                  lambda: self.selected() == (path, Path(path).name))
            def verify_frame():
                image = pixels(MAIN)
                viewport = box(self.board)
                l, t, r, b = viewport
                x, y, right, bottom = box(self.cards[path])
                visible = max(l, x), max(t, y), min(r, right), min(b, bottom)
                if visible[0] >= visible[2] or visible[1] >= visible[3]:
                    return False
                # Scan the actual screen. The selected frame must surround the same
                # file its breadcrumb names, including when the viewport clips it.
                panel = image.crop(viewport)
                rgb = ImageChops.difference(panel, Image.new("RGB", panel.size, ACCENT)).split()
                mask = ImageChops.lighter(rgb[0], ImageChops.lighter(rgb[1], rgb[2]))
                mask = mask.point(lambda component: 255 if component == 0 else 0)
                outline = mask.getbbox()
                if not outline:
                    return False
                expected = visible[0] - l, visible[1] - t, visible[2] - l, visible[3] - t
                if any(abs(a - b) > 2 for a, b in zip(outline, expected)):
                    return False
                width, height = visible[2] - visible[0], visible[3] - visible[1]
                perimeter = width * sum(t <= edge < b for edge in (y, bottom - 1))
                perimeter += height * sum(l <= edge < r for edge in (x, right - 1))
                if mask.histogram()[255] < perimeter * .7:
                    return False
                if centered:
                    for start, end, low, high in ((x, right, l, r), (y, bottom, t, b)):
                        if end - start <= high - low:
                            if start < low - 1 or end > high + 1:
                                return False
                        elif not low - 1 <= start <= low + 21:
                            return False
                return image
            screenshot = until("selected border is missing, misplaced or outside the viewport", verify_frame)
            screenshot.save(OUTPUT / (step + ".png"))
        except AssertionError as err:
            pixels(MAIN).save(OUTPUT / (step + ".failed.png"))
            raise AssertionError(f"{step}: expected {path}; selected {self.selected()}; "
                                 f"viewport {box(self.board)}; card {box(self.cards[path])}: {err}") from err
        print(f"{step}: {self.zoom()}% {path}", flush=True)

    def mark(self, step, path, target, via, checked):
        def checkbox():
            header = self.cards[path].get_child_at_index(0)
            return header.get_child_at_index(1)
        is_checked = lambda: checkbox().get_state_set().contains(Atspi.StateType.CHECKED)
        assert is_checked() != checked, f"{step}: unexpected initial marker on {path}"
        total = self.count_label.get_name()
        zoom = self.zoom()
        if via == "card":
            click(*self.visible_point(checkbox()))
        elif via == "sticky":
            click(*midpoint(self.sticky))
        else:
            xdo("key", "v")
        until(f"{step}: viewed count did not change", lambda: self.count_label.get_name() != total)
        self.load_cards()
        assert is_checked() == checked, f"{step}: marker for {path} was not saved"
        self.assert_selection(step, target)
        assert self.zoom() == zoom, f"{step}: changing Viewed changed zoom"

    def key(self, step, key, target, boundary=False):
        previous = box(self.cards[target])
        zoom = self.zoom()
        xdo("key", key)
        self.assert_selection(step, target)
        assert self.zoom() == zoom, f"{step}: arrow changed zoom"
        if boundary:
            assert box(self.cards[target]) == previous, f"{step}: camera moved at map boundary"


def traverse(m, phase, row):
    for i in range(1, len(row)):
        m.key(f"{phase}-right-{i}", "Right", row[i])
    m.key(f"{phase}-right-edge", "Right", row[-1], boundary=True)
    for i in reversed(range(len(row) - 1)):
        m.key(f"{phase}-left-{i}", "Left", row[i])
    m.key(f"{phase}-left-edge", "Left", row[0], boundary=True)


def rapid_viewed_pan(m, current, next_file):
    # Compare two real input sequences from the same selected file and marker
    # state. In the second there is no frame or key delay between V and pan.
    m.select_title(current)
    m.mark("rapid-setup-unmark", current, current, "key", False)
    m.select_title(current, double=True)
    until("double-click did not zoom into the pan case", lambda: m.zoom() == 100)
    m.load_cards()
    m.mark("settled-v", current, next_file, "key", True)
    centered = box(m.cards[next_file])
    xdo("key", "shift+Left")
    until("settled Shift+Left did not pan", lambda: box(m.cards[next_file])[0] > centered[0] + 10)
    m.assert_selection("settled-v-pan", next_file, centered=False)
    reference = box(m.cards[next_file])
    m.select_title(current)
    m.mark("rapid-restore-unmark", current, current, "key", False)
    xdo("key", "--delay", 0, "v", "shift+Left")
    until("rapid V did not advance", lambda: m.selected()[0] == next_file)
    m.load_cards()
    m.assert_selection("rapid-v-pan", next_file, centered=False)
    # Give the deferred centering callback time to run; a late callback previously
    # overwrote the second input even though selection itself had advanced.
    time.sleep(.25)
    actual = box(m.cards[next_file])
    assert actual == reference, f"rapid V then Shift+Left: viewport {actual}, settled equivalent {reference}"


def open_navigation_map():
    Atspi.init()
    win = window_id()
    xdo("windowfocus", win)
    desktop = Atspi.get_desktop(0)
    window = until("review window missing from accessibility tree",
                   lambda: find(desktop, "frame", MAIN), timeout=15)
    recent = until("fixture's recent review missing",
                   lambda: find(window, "push button", "Pr · " + TITLE))
    click(*midpoint(recent))
    button = until("PR overview did not load", lambda: find(window, "push button", "Open diff map"))
    click(*midpoint(button))
    until("review clone did not finish loading",
          lambda: box(button)[2] > 0 and button.get_name() == "Back to overview", timeout=180)
    return Map(window)


def navigate(_):
    m = open_navigation_map()
    window = m.window
    # At full zoom the old geometric navigation skipped directly from the first
    # Frontend card to Other, bypassing both intervening populated columns.
    front, shared, back, other = (LANES[lane] for lane in ("Frontend", "Shared", "Backend", "Other"))
    m.key("initial-right", "Right", shared[0])
    m.key("initial-left", "Left", front[0])

    # At full size the third Frontend card starts beside the third Shared
    # tile, fifth Backend tile and first Other tile. The latter spans several
    # rows: navigating back must recover the original row through that tile.
    row = (front[2], shared[2], back[4], other[0])
    m.fit()
    m.select_title(row[0])
    m.assert_selection("fit-start", row[0], centered=False)
    traverse(m, "fit", row)
    m.select_title(row[0], double=True)
    until("double-click did not return to card zoom", lambda: m.zoom() == 100)
    m.load_cards()
    m.assert_selection("zoom-start", row[0])
    traverse(m, "zoom", row)
    before = box(m.cards[row[0]])
    xdo("mousemove", 820, 650, "mousedown", 2, "mousemove", 748, 603)
    until("middle drag did not pan the map", lambda: box(m.cards[row[0]]) != before)
    xdo("mouseup", 2)
    m.assert_selection("panned-start", row[0], centered=False)
    traverse(m, "panned", row)

    m.key("up-to-second", "Up", front[1])
    m.key("up-to-first", "Up", front[0])
    m.key("upper-boundary", "Up", front[0], boundary=True)
    m.key("down-to-second", "Down", front[1])
    m.key("up-to-first-again", "Up", front[0])

    # One already-viewed neighbor, then three. The sticky filename and the
    # selected border must both move to the next unread file after reflow.
    m.mark("unmark-stays", front[0], front[0], "key", False)
    m.mark("mark-skip-one", front[0], front[2], "key", True)
    m.mark("mark-skip-three", front[2], front[6], "key", True)
    m.fit()
    # A card checkbox on a different lane advances from the card clicked.
    m.mark("card-viewed", shared[3], shared[4], "card", True)
    m.select_title(other[0])
    m.key("down-within-other", "Down", other[1])
    m.key("down-to-last", "Down", other[2])
    m.key("lower-boundary", "Down", other[2], boundary=True)
    m.mark("no-lower-unviewed", other[2], other[2], "key", True)
    m.mark("sticky-unmark", other[2], other[2], "sticky", False)
    # The middle Other file is an unviewed spec. Hiding specs must skip it.
    click(*midpoint(find(window, "toggle button", "Hide specs")))
    until("spec filter did not update the count", lambda: "/ 123 viewed" not in m.count_label.get_name())
    m.load_cards()
    assert other[1] not in m.cards
    m.select_title(other[0])
    m.mark("skip-hidden-spec", other[0], other[2], "sticky", True)
    rapid_viewed_pan(m, other[0], other[2])


def run_navigation(command):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "failure.png").unlink(missing_ok=True)
    for old_failure in OUTPUT.glob("*.failed.png"):
        old_failure.unlink()
    with tempfile.TemporaryDirectory() as directory:
        home = Path(directory)
        env = prepare_pr_28118(home)
        exercise(command, env, navigate, None, failure_directory=OUTPUT)


if __name__ == "__main__":
    run_navigation([sys.argv[1]])
