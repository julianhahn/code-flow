"""Observe actual painted positions throughout keyboard pans and interactive zoom."""
import json
import re
from pathlib import Path
import sys
import tempfile
import time

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk
from PIL import Image, ImageChops

from navigation import ACCENT, box, children, find, midpoint, open_navigation_map, until
from pr_28118 import LANES, prepare_pr_28118
from scenarios import RESULTS, exercise, xdo

OUTPUT = RESULTS / "motion"


def capture(area):
    x, y, r, b = area
    frame = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(), x, y, r - x, b - y)
    return Image.frombytes("RGB", (r - x, b - y), frame.get_pixels(), "raw", "RGB", frame.get_rowstride())


def border(image):
    # The selected card has the only blue border on the map. Allow a small
    # difference from its CSS color while the preview is being resampled.
    if min(image.size) <= 8:
        rgb = ImageChops.difference(image, Image.new("RGB", image.size, ACCENT)).split()
        maximum = ImageChops.lighter(rgb[0], ImageChops.lighter(rgb[1], rgb[2]))
        return maximum.point(lambda intensity: 255 if intensity == 0 else 0).getbbox()
    red, green, blue = image.split()
    # Resampling can dim a thin edge against the map background. Its blue
    # hue still distinguishes it from the gray connectors and green diff text.
    red_gap = ImageChops.subtract(blue, red).point(lambda v: 255 if v > 48 else 0)
    green_gap = ImageChops.subtract(blue, green).point(lambda v: 255 if v > 22 else 0)
    mask = ImageChops.darker(red_gap, green_gap)
    # File links also use shades of blue. Across this view only the selected
    # border has long unbroken horizontal runs. Read both edges from those rows.
    sums = list(mask.resize((1, mask.height), Image.Resampling.BOX).getdata())
    peak = max(sums)
    if peak < 12:
        return None
    rows = []
    for y, value in enumerate(sums):
        if value >= .55 * peak:
            buf = mask.crop((0, y, mask.width, y + 1)).tobytes()
            spans = [m.span() for m in re.finditer(b"\xff{50,}", buf)]
            if spans:
                l, r = max(spans, key=lambda pair: pair[1] - pair[0])
                rows.append((l, y, r, y + 1))
    if rows:
        return min(p[0] for p in rows), rows[0][1], max(p[2] for p in rows), rows[-1][3]


def key(direction):
    xdo("key", "--delay", 0, "shift+" + direction)


class Measurement:
    def __init__(self, directory):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "failure.png").unlink(missing_ok=True)
        self.failures = []
        self.traces = {}

    def check(self, condition, message):
        if not condition:
            self.failures.append(message)

    def record(self, name, area, events, tail=.5, ready=None):
        """Schedule actual input while sampling X11. Keep every change in paint."""
        events = list(events)
        observed, inputs = [], []
        start = time.monotonic()
        last_paint = last_input = start
        next_poll = start
        ready_since = None
        while True:
            if not events and time.monotonic() - last_input >= tail:
                if ready is None:
                    break
                now = time.monotonic()
                if ready_since is None and now >= next_poll:
                    if ready():
                        ready_since = time.monotonic()
                    next_poll = now + .05
                if ready_since is not None and now - max(ready_since, last_paint) >= .15:
                    break
                assert now - last_input < 8, name + ": display did not settle"
            frame = capture(area)
            bounds = border(frame)
            now = time.monotonic()
            ms = round((now - start) * 1000)
            if not observed or bounds != observed[-1][1]:
                observed.append((ms, bounds, frame))
                last_paint = now
            if events and (not inputs or now - last_input >= events[0][0]):
                _, label, action = events.pop(0)
                inputs.append((ms, label))
                action()
                last_input = time.monotonic()
            time.sleep(.002)
        self.traces[name] = {
            "region": area, "inputs_ms": inputs, "paint_ms_bounds": [(ms, b) for ms, b, _ in observed],
            "observed_until_ms": round((time.monotonic() - start) * 1000),
        }
        (self.directory / "timings.json").write_text(json.dumps(self.traces, indent=2) + "\n")
        # Save the first, last and intermediate real captures together for inspection.
        indices = sorted({round(i * (len(observed) - 1) / 6) for i in range(7)})
        strip = Image.new("RGB", (frame.width, frame.height * len(indices)))
        for row, index in enumerate(indices):
            strip.paste(observed[index][2], (0, row * frame.height))
        strip.save(self.directory / (name + ".png"))
        return [(ms, b) for ms, b, _ in observed]


def readings(trace, field):
    return [(ms, field(bounds)) for ms, bounds in trace if bounds is not None]


def check_zoom_handoff(test, name, widths):
    # A new frame after the gesture has stood still must not reveal a large
    # scale correction when the real widget tree replaces its preview.
    last_input = test.traces[name]["inputs_ms"][-1][0]
    for (t0, w0), (t1, w1) in zip(widths, widths[1:]):
        if t0 >= last_input and t1 - t0 > 200:
            test.check(abs(w1 - w0) <= max(4, w0 * .025), f"{name}: late scale jump {w0}→{w1}px")


def check_pan(test, name, trace, edge, distance, animated):
    path = readings(trace, lambda bounds: bounds[edge])
    assert path, name + ": selected border disappeared"
    start, end = path[0][1], path[-1][1]
    test.check(abs(end - start - distance) <= 2, f"{name}: traveled {end-start}px, expected {distance}px")
    test.check(all(bounds for _, bounds in trace), name + ": card vanished during pan")
    sign = 1 if distance > 0 else -1
    positions = [(value - start) * sign for _, value in path]
    test.check(all(b >= a - 1 for a, b in zip(positions, positions[1:])), name + ": motion reversed")
    middle = {p for p in positions if 2 < p < abs(distance) - 2}
    test.check(len(middle) >= 2 if animated else not middle,
               f"{name}: {len(middle)} intermediate positions, animations={animated}")
    if animated:
        largest = max((b - a for a, b in zip(positions, positions[1:])), default=0)
        test.check(largest < .72 * abs(distance), f"{name}: a single frame jumped {largest}px")
    print(f"{name}: {start} → {end}px, {len(middle)} intermediate positions, settled by {path[-1][0]} ms")
    return end


def pans(m, path, test, animated):
    l, t, r, b = box(m.board)
    x, y = midpoint(m.cards[path])
    horizontal = (l, y - 4, r, y + 4)
    vertical = (x - 4, t, x + 4, b)
    dx, dy = round(.4 * (r - l)), round(.4 * (b - t))
    for forward, backward, area, edge, step in (
            ("Right", "Left", horizontal, 2, dx), ("Down", "Up", vertical, 3, dy)):
        for direction, amount in ((forward, -step), (backward, step)):
            trace = test.record("pan-" + direction, area, [(0, direction, lambda: key(direction))])
            check_pan(test, "pan-" + direction, trace, edge, amount, animated)

    if animated:
        # The second and third inputs arrive during the first step. Two rights
        # and one left must finish exactly one viewport step from the origin.
        burst = ("Right", "Right", "Left")
        trace = test.record("pan-retarget", horizontal,
                            [(.035, k, lambda k=k: key(k)) for k in burst])
        values = [v for _, v in readings(trace, lambda p: p[2])]
        origin, dest = values[0], values[0] - dx
        test.check(abs(values[-1] - dest) <= 2, "repeat/reversal did not finish at the accumulated endpoint")
        test.check(min(values) >= dest - 2 and max(values) <= origin + 2,
                   "repeat/reversal overshot the final viewport step")
        trace = test.record("pan-reverse", horizontal,
                            [(0, "Left", lambda: key("Left")), (.045, "Right", lambda: key("Right"))])
        values = [v for _, v in readings(trace, lambda p: p[2])]
        test.check(max(values) >= dest + 5 and min(values) >= dest - 2 and max(values) <= origin + 2,
                   "direction reversal did not move inside its endpoints")
        test.check(abs(values[-1] - dest) <= 2, "direction reversal did not settle back at the start")
        key("Left")
        until("camera did not return to its start",
              lambda: abs(box(m.cards[path])[2] - (l + origin)) <= 2)
    test.check(m.selected()[0] == path and m.zoom() == 100, "Shift+arrow changed the active file or zoom")
    if animated:
        # A plain selection arrow takes over the viewport during an in-flight
        # pan. The old callback must not pull it away from the new card later.
        front = LANES["Frontend"][0]
        xdo("key", "--delay", 0, "Left")
        until("plain Left did not select Frontend", lambda: m.selected()[0] == front)
        front_centered = box(m.cards[front])
        xdo("key", "--delay", 0, "Right")
        until("plain Right did not select Shared", lambda: m.selected()[0] == path)
        test.record("pan-interrupted", horizontal,
                    [(0, "pan-right", lambda: key("Right")),
                     (.055, "select-left", lambda: xdo("key", "--delay", 0, "Left"))])
        test.check(m.selected()[0] == front and box(m.cards[front]) == front_centered,
                   "interrupted pan moved the camera away from the newly selected file")
        # Pan farther left at the first column, where GTK is already at its lower bound.
        key("Left")
        time.sleep(.23)
        test.check(box(m.cards[front]) == front_centered, "pan at the outer edge moved past its bound")
        xdo("key", "--delay", 0, "Right")
        until("could not return to Shared", lambda: m.selected()[0] == path)


def outer_limits(m):
    # From the bottom of the long Backend column, oversubscribe both
    # scrollbars in one burst. Read GTK's actual legal endpoints.
    back, shared = LANES["Backend"], LANES["Shared"]
    xdo("key", "--delay", 0, "Right", *(["Down"] * len(back)))
    until("could not reach final Backend card", lambda: m.selected()[0] == back[-1])
    bars = [node for node in children(m.board.get_parent()) if node.get_role_name() == "scroll bar"]
    assert len(bars) == 2
    xdo("key", "--delay", 0, *(["shift+Right", "shift+Down"] * 16))
    at_limit = lambda: all(abs(a.get_maximum_value() - a.get_current_value()) < 1 for a in bars)
    try:
        until("diagonal pan stopped before the lower right limit", at_limit)
    except AssertionError as err:
        actual = [(a.get_current_value(), a.get_maximum_value()) for a in bars]
        raise AssertionError(f"scrollbar (value, limit) after diagonal burst: {actual}") from err
    xdo("key", "--delay", 0, "shift+Right", "shift+Down")
    time.sleep(.23)
    assert at_limit() and m.selected()[0] == back[-1], "camera or selection drifted at the outer limit"
    xdo("key", "--delay", 0, "Left", *(["Up"] * len(shared)))
    until("could not return from the outer limit", lambda: m.selected()[0] == shared[0])


def zoom_settled(m):
    m.zoom_label.clear_cache()
    return "Zooming" not in m.zoom_label.get_name()


def painted_zoom_status(m):
    # Observe the real toolbar without polling the app on its UI thread. During
    # a busy relayout a synchronous accessibility read can block several frames.
    x, y, _, bottom = box(m.zoom_label)
    percent_area = (x, y, x + 40, bottom)
    count_area = box(m.count_label)
    original_percent, original_count = capture(percent_area), capture(count_area)
    def settled():
        current_percent = capture(percent_area)
        current_count = capture(count_area)
        return (ImageChops.difference(current_percent, original_percent).getbbox() is not None and
                ImageChops.difference(current_count, original_count).getbbox() is None)
    return settled


def zoom(m, test):
    path = LANES["Backend"][16]
    xdo("key", "--delay", 0, "Right", *(["Down"] * 16))
    until("zoom setup did not reach the interior target", lambda: m.selected()[0] == path)
    # Place the selected card away from the viewport center so a wheel zoom
    # around the wrong anchor is visible. Keep the pointer on its midpoint.
    x, y = midpoint(m.cards[path])
    xdo("mousemove", x, y, "mousedown", 2, "mousemove", x + 120, y + 55, "mouseup", 2)
    until("setup drag did not move the map", lambda: midpoint(m.cards[path])[0] >= x + 115)
    x, y = midpoint(m.cards[path])
    xdo("mousemove", x, y, "keydown", "Control_L")
    l, t, r, b = box(m.board)
    area = (l, max(t, y - 125), r, min(b, y + 125))
    try:
        events = [(.045, "wheel-out", lambda: xdo("click", "--delay", 0, 5)) for _ in range(7)]
        events += [(.045, "wheel-in", lambda: xdo("click", "--delay", 0, 4)) for _ in range(3)]
        # Let the first gesture reach its scale, then change it again while the
        # replacement 123-file widget tree is being prepared in the background.
        # Individual wheel events must reach the desktop. GTK may merge
        # zero-delay discrete scroll events, and a single smaller scale can
        # produce nearly the same hinted filename width on a collapsed card.
        events += [(.16 if i == 0 else .04, "staging-wheel-out", lambda: xdo("click", "--delay", 0, 5))
                   for i in range(4)]
        events += [(.05, "staging-wheel-in", lambda: xdo("click", "--delay", 0, 4)) for _ in range(4)]
        trace = test.record("zoom-wheel", area, events, tail=.35,
                            ready=painted_zoom_status(m))
    finally:
        xdo("keyup", "Control_L")
    pulses = test.traces["zoom-wheel"]["inputs_ms"]
    widths = readings(trace, lambda p: p[2] - p[0])
    assert widths, "selected border missing throughout zoom"
    initial = widths[0][1]
    check_zoom_handoff(test, "zoom-wheel", widths)
    live = [(ms, w) for ms, w in widths if ms < pulses[7][0] and w < initial - 3]
    test.check(len({w for _, w in live}) >= 2, "wheel zoom did not visibly shrink before the burst reversed")
    test.check(bool(widths) and widths[-1][1] > min(w for _, w in widths) + 8,
               "wheel reversal did not grow the painted card")
    # Check the anchor during the active gesture. GTK will remeasure the actual
    # labels at settlement, so the final card size may differ from a pure scale.
    drift = [max(abs(area[0] + (p[0] + p[2]) / 2 - x), abs(area[1] + (p[1] + p[3]) / 2 - y))
             for ms, p in trace if p and p[3] - p[1] > 6 and live and live[0][0] <= ms < pulses[-1][0]]
    test.check(not drift or max(drift) <= 6, f"wheel preview drifted up to {max(drift, default=0):.1f}px from cursor")
    test.check(all(p for _, p in trace), "selected card disappeared during wheel zoom")
    start_stage = pulses[10][0]
    before_stage = [w for ms, w in widths if ms <= start_stage][-1]
    staged = [w for ms, w in widths if start_stage < ms < pulses[14][0] + 100]
    test.check(staged and min(staged) < before_stage - 3,
               "a new wheel input could not move the map while GTK prepared the final layout")
    final_zoom = round(100 * 1.08 ** -4)
    until("wheel zoom did not settle on its accumulated target", lambda: m.zoom() == final_zoom)
    m.load_cards()
    widget = box(m.cards[path])
    painted = trace[-1][1]
    matches = painted and all(abs(value - expected) <= 2 for value, expected in
                              zip(painted, (widget[0] - area[0], widget[1] - area[1],
                                            widget[2] - area[0], widget[3] - area[1])))
    test.check(matches, "settled wheel preview did not give way to the actual selected GTK card")
    test.check(m.selected()[0] == path, "wheel changed the selected file")
    test.check(max(abs(a - b) for a, b in zip(midpoint(m.cards[path]), (x, y))) <= 3,
               "the final interactive card lost the pointer anchor")
    reacted_ms = next((ms - start_stage for ms, w in widths if ms > start_stage and w < before_stage - 3), None)
    print(f"zoom-wheel: {initial} → {min(w for _, w in widths)} → {widths[-1][1]}px, "
          f"{len(live)} in-burst widths, anchor drift {max(drift, default=0):.1f}px, staging retarget {reacted_ms} ms")
    toolbar_zoom(m, test, True)
    # Exercise the real card widget after the transformed preview has gone.
    other = LANES["Backend"][17]
    m.select_title(other)
    until("rendered file could not be selected after zoom", lambda: m.selected()[0] == other)


def queued_navigation(m):
    # Compare an immediate zoom + three keys with the same actions after zoom
    # has settled. V changes geometry and Shift+Left moves relative to that reflow.
    start, marked, target = LANES["Backend"][17:20]
    assert m.selected()[0] == start
    initial = m.count_label.get_name()
    reset = midpoint(find(m.window, "push button", "100%"))
    smaller = midpoint(find(m.window, "push button", "−"))

    def button(point):
        xdo("mousemove", *point, "click", "--delay", 0, 1)

    def settled(percent):
        until(f"zoom did not settle at {percent}%", lambda: zoom_settled(m) and m.zoom() == percent,
              timeout=8)
        m.load_cards()

    button(reset)
    settled(100)
    xdo("key", "--delay", 0, "Down")
    until("control sequence did not select the next row", lambda: m.selected()[0] == marked)
    xdo("key", "--delay", 0, "v")
    until("control sequence did not advance after Viewed", lambda: m.selected()[0] == target)
    m.load_cards()
    count = m.count_label.get_name()
    assert count != initial
    l, _, r, _ = box(m.board)
    centered = box(m.cards[target])
    key("Left")
    until("control pan did not settle",
          lambda: abs(box(m.cards[target])[0] - centered[0] - (r - l) * .4) < 2)
    # The curve can be within a pixel before its final frame. Wait until the
    # control has landed on its exact endpoint before saving that reference.
    time.sleep(.20)
    reference = box(m.cards[target])
    xdo("key", "--delay", 0, "Up", "v")
    until("could not restore the marker", lambda: m.count_label.get_name() == initial)
    xdo("key", "--delay", 0, "Up")
    until("could not restore the original selection", lambda: m.selected()[0] == start)
    button(smaller)
    settled(83)
    xdo("mousemove", *reset, "click", "--delay", 0, 1,
        "key", "--delay", 0, "Down", "v", "shift+Left")
    until("zoom lost the queued selection or Viewed input",
          lambda: m.selected()[0] == target and m.count_label.get_name() == count, timeout=8)
    m.load_cards()
    try:
        until("zoom lost the pan or applied it against the wrong layout",
              lambda: box(m.cards[target]) == reference)
    except AssertionError as err:
        raise AssertionError(f"queued zoom camera {box(m.cards[target])}, settled control {reference}") from err
    print("zoom-queued-inputs: selection, Viewed count and viewport match the settled control")


def preview_click(m):
    first, second = LANES["Backend"][16:18]
    xdo("key", "--delay", 0, "Up", "Up", "Up")
    until("click setup did not select the first card", lambda: m.selected()[0] == first)
    m.load_cards()
    x, y = midpoint(m.titles[second])
    # Use the filename itself as the wheel pivot: it stays under the pointer
    # throughout the preview, so clicking it must select that file.
    region = box(m.board)
    original = border(capture(region))
    assert original, "initial selected border missing"
    xdo("mousemove", x, y, "keydown", "Control_L", "click", "--delay", 0, 4, "keyup", "Control_L")
    def visibly_zoomed():
        current = border(capture(region))
        return current and current[2] - current[0] > (original[2] - original[0]) * 1.05
    until("preview did not visibly zoom before click", visibly_zoomed)
    assert not zoom_settled(m), "click missed the interactive zoom preview"
    patch = capture((x - 4, y - 4, x + 5, y + 5))
    assert (43, 35, 16) in patch.getdata(), "filename under pointer moved before the preview click"
    start = time.monotonic()
    xdo("click", "--delay", 0, 1)
    def preview_selected():
        if m.selected()[0] != second:
            return False
        shape = border(capture(region))
        return (shape and shape[0] <= x - region[0] < shape[2] and
                shape[1] <= y - region[1] < shape[3])
    until("preview did not show the clicked filename and border", preview_selected, timeout=8)
    assert not zoom_settled(m), "selection feedback waited for the replacement widget tree"
    latency_ms = round((time.monotonic() - start) * 1000)
    until("interactive board did not settle after click", lambda: zoom_settled(m), timeout=8)
    m.load_cards()
    card = box(m.cards[second])
    expected = tuple(value - region[i % 2] for i, value in enumerate(card))
    def outlined():
        painted = border(capture(region))
        return painted and all(abs(a - b) <= 2 for a, b in zip(painted, expected))
    until("the file clicked during zoom did not receive its selected border", outlined)
    print(f"zoom-preview-click: filename and border changed within {latency_ms} ms and survived settlement")


def toolbar_zoom(m, test, animated):
    path = m.selected()[0]
    start_zoom = m.zoom()
    l, t, r, b = box(m.board)
    button = find(m.window, "push button", "+")
    bx, by = midpoint(button)
    trace = test.record("zoom-button", (l, t, r, b),
                        [(0, "zoom-in", lambda: xdo("mousemove", bx, by, "click", "--delay", 0, 1))], tail=.35,
                        ready=painted_zoom_status(m))
    widths = readings(trace, lambda p: p[2] - p[0])
    sizes = [w for _, w in widths]
    mids = {w for w in sizes if sizes[0] + 2 < w < sizes[-1] - 2}
    if animated:
        check_zoom_handoff(test, "zoom-button", widths)
        test.check(all(p for _, p in trace), "selected card disappeared during toolbar zoom")
    test.check(len(mids) >= 2 if animated else not mids,
               f"toolbar zoom {sizes[0]}→{sizes[-1]} has {len(mids)} intermediate widths, animations={animated}")
    test.check(abs(m.zoom() - start_zoom * 1.2) <= 1, "toolbar zoom did not reach requested scale")
    m.load_cards()
    test.check(m.selected()[0] == path, "toolbar zoom changed the selected file")


def run_motion(command):
    for animated in (True, False):
        test = Measurement(OUTPUT / ("enabled" if animated else "reduced"))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = prepare_pr_28118(home)
            if animated:
                settings = home / ".config/gtk-3.0/settings.ini"
                settings.write_text(settings.read_text().replace("animations=false", "animations=true"))

            def scenario(_):
                m = open_navigation_map()
                path = LANES["Shared"][0]
                xdo("key", "Right")
                until("initial selection missing", lambda: m.selected()[0] == path)
                until("initial camera is not centered on the selected file",
                      lambda: abs(midpoint(m.cards[path])[0] - midpoint(m.board)[0]) <= 2)
                pans(m, path, test, animated)
                if animated:
                    outer_limits(m)
                    zoom(m, test)
                    queued_navigation(m)
                    preview_click(m)
                else:
                    toolbar_zoom(m, test, False)
                assert not test.failures, "\n".join(test.failures)

            exercise(command, env, scenario, None, failure_directory=test.directory)


if __name__ == "__main__":
    run_motion([sys.argv[1]])
