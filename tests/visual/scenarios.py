"""Drive the shipping app on X11 and compare complete window pixels with pre-port goldens."""
import contextlib
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf
from PIL import Image, ImageChops

from fixture import prepare, prepare_flow

RESULTS = Path("/results")
GOLDENS = Path("/goldens")
MODE = sys.argv[1]
MAIN = "Code Flow — Review"
HELP = "Code Flow — Shortcuts"


def xdo(*args):
    return subprocess.check_output(["xdotool", *map(str, args)], text=True).strip()


def wait(description, test):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        result = test()
        if result:
            return result
        time.sleep(.08)
    raise AssertionError("Timed out waiting for " + description)


def windows_named(title):
    try:
        return xdo("search", "--onlyvisible", "--name", "^" + re.escape(title) + "$").splitlines()
    except subprocess.CalledProcessError:
        return []


def window_id(title=MAIN):
    return wait(title, lambda: windows_named(title))[0]


def assert_desktop_icon(title):
    if MODE != "compare":
        return
    # Read exactly what a taskbar gets from the X server, including transparency.
    window = window_id(title)
    props = subprocess.check_output([
        "xprop", "-notype", "-id", window, "-len", "1048576",
        "-f", "_NET_WM_ICON", "32c", r" = $0+\n", "WM_CLASS", "_NET_WM_ICON",
    ], text=True).splitlines()
    assert props[0] == 'WM_CLASS = "code-flow", "code-flow"', (title, props[0])
    assert props[1].startswith("_NET_WM_ICON = "), title + ": missing window icon"
    values = [int(v) for v in props[1].removeprefix("_NET_WM_ICON = ").split(", ")]
    reference = GdkPixbuf.Pixbuf.new_from_file(str(Path(__file__).resolve().parents[2] / "code-flow.svg"))
    size = (reference.get_width(), reference.get_height())
    image = Image.frombytes("RGBA", size, reference.get_pixels(), "raw", "RGBA", reference.get_rowstride())
    argb = [(a << 24) | (r << 16) | (g << 8) | b for r, g, b, a in image.getdata()]
    assert values == [*size, *argb], title + ": taskbar icon differs from code-flow.svg"


def click(x, y):
    xdo("mousemove", x, y, "click", 1)


def pixels(title):
    # Capture the real window from the X server, including native child windows.
    win = window_id(title)
    geometry = dict(line.split("=", 1) for line in
                    xdo("getwindowgeometry", "--shell", win).splitlines() if "=" in line)
    x, y, w, h = (int(geometry[key]) for key in ("X", "Y", "WIDTH", "HEIGHT"))
    root = Gdk.get_default_root_window()
    frame = Gdk.pixbuf_get_from_window(root, x, y, w, h)
    return Image.frombytes("RGB", (w, h), frame.get_pixels(), "raw", "RGB", frame.get_rowstride())


def mismatch_mask(expected, actual, tolerance=0):
    r, g, b = ImageChops.difference(expected, actual).split()
    return ImageChops.lighter(r, ImageChops.lighter(g, b)).point(lambda v: 255 if v > tolerance else 0)


class Screenshots:
    def __init__(self):
        self.failures = []
        self.states = []
        self.last = None

    def capture(self, name, title=MAIN):
        # Keep hover highlights, tooltips and insertion cursor blinking deterministic.
        xdo("mousemove", 1599, 1199)
        time.sleep(.55)
        actual = pixels(title)
        self.last = actual
        self.states.append(name)
        dest = RESULTS / MODE
        dest.mkdir(parents=True, exist_ok=True)
        actual.save(dest / (name + ".png"))
        (dest / (name + ".diff.png")).unlink(missing_ok=True)
        path = GOLDENS / (name + ".png")
        if MODE == "record-legacy":
            actual.save(path)
            print("recorded", name, actual.size)
            return
        if not path.exists():
            self.failures.append(name + ": missing reference")
            return
        with Image.open(path) as original:
            expected = original.convert("RGB")
        if expected.size != actual.size:
            self.failures.append(f"{name}: size {actual.size}, expected {expected.size}")
            return
        # Subpixel interpolation differs by one RGB level at 115% in the flow viewer.
        tolerance = 1 if name in ("16-flow-zoom", "17-flow-pan") else 0
        mask = mismatch_mask(expected, actual, tolerance)
        changed = mask.histogram()[255]
        if changed:
            # Highlight all changed pixels on a dimmed copy of the captured screen.
            overlay = Image.blend(actual, Image.new("RGB", actual.size), .58)
            overlay.paste((255, 65, 91), mask=mask)
            overlay.save(dest / (name + ".diff.png"))
            self.failures.append(f"{name}: {changed:,} changed pixels ({changed / (actual.width * actual.height):.2%})")
        print(name, changed, "changed pixels")

    def finish(self):
        expected_states = {path.stem for path in GOLDENS.glob("*.png")}
        if expected_states != set(self.states):
            self.failures.append("Captured states differ from goldens: " + str(expected_states ^ set(self.states)))
        if self.failures:
            raise AssertionError("\n".join(self.failures))


def frame_corner(image):
    # The selected card's lower-right border stays visible throughout this drag.
    # Restrict the scan to the viewport, excluding toolbar and sidebar accents.
    left, top, right, bottom = 10, 315, 920, 895
    panel = image.crop((left, top, right, bottom))
    channels = ImageChops.difference(panel, Image.new("RGB", panel.size, (88, 166, 255))).split()
    maximum = ImageChops.lighter(channels[0], ImageChops.lighter(channels[1], channels[2]))
    rect = maximum.point(lambda value: 255 if value == 0 else 0).getbbox()
    if rect is None:
        return None
    return left + rect[2] - 1, top + rect[3] - 1


def open_map():
    click(85, 96)
    wait("the selected file's blue frame on the finished diff map",
         lambda: frame_corner(pixels(MAIN)))


def wait_for_ink(description, region):
    def painted():
        return any(low != high for low, high in pixels(MAIN).crop(region).getextrema())
    wait(description, painted)


def scenario(s):
    window_id()
    window_id(HELP)
    assert_desktop_icon(MAIN)
    assert_desktop_icon(HELP)
    s.capture("01-first-run-help", HELP)
    # The default button is the only blue fill in the shortcut window.
    image = s.last
    accent = (31, 111, 235)
    points = [(x, y) for y in range(0, image.height, 3) for x in range(0, image.width, 3)
              if image.getpixel((x, y)) == accent]
    if not points:
        raise AssertionError("Cannot find Got it button in shortcut window")
    xdo("mousemove", "--window", window_id(HELP),
        sum(x for x, _ in points) // len(points), sum(y for _, y in points) // len(points), "click", 1)
    wait("shortcut window to close", lambda: not windows_named(HELP))
    xdo("windowfocus", window_id())
    s.capture("02-dashboard")
    click(478, 148)  # recent offline PR
    wait("Open diff map button", lambda: pixels(MAIN).getpixel((20, 90)) == (31, 111, 235))
    s.capture("03-pr-overview")
    open_map()
    s.capture("04-diff-map")
    original = s.last
    xdo("mousemove", 790, 710, "mousedown", 2)
    corner = frame_corner(original)
    if corner is None:
        raise AssertionError("The selected file has no visible blue frame")
    start_x, start_y = corner
    for x, y in ((770, 690), (720, 650), (675, 615), (620, 572)):
        xdo("mousemove", x, y)
        expected = (start_x + x - 790, start_y + y - 710)
        deadline = time.monotonic() + .25
        while frame_corner(pixels(MAIN)) != expected:
            if time.monotonic() >= deadline:
                raise AssertionError(f"Selected card did not track drag to {expected} before mouse release")
            time.sleep(.005)
    xdo("mouseup", 2)
    s.capture("05-middle-drag-pan")
    region = (10, 360, 920, 875)
    if mismatch_mask(original.crop(region), s.last.crop(region)).histogram()[255] < 1000:
        raise AssertionError("Middle drag did not move the rendered map")
    click(238, 145)
    s.capture("06-fit-all")
    click(340, 145)
    s.capture("07-hide-specs")
    click(156, 145)
    xdo("key", "Down")
    s.capture("08-arrow-navigation")
    xdo("key", "v")
    s.capture("09-viewed")
    xdo("key", "ctrl+f")
    xdo("type", "publish")
    s.capture("10-search")
    xdo("key", "Escape")
    click(1060, 150)
    s.capture("11-files")
    click(1270, 150)
    # Enter text through the actual editor.
    xdo("mousemove", 1110, 435, "click", 1, "type", "Check the publication status.")
    s.capture("12-notes")
    click(1160, 150)
    # The completed count extends past the short loading message.
    wait_for_ink("completed conversation count", (1255, 226, 1320, 244))
    s.capture("13-conversations")
    click(1300, 30)
    s.capture("14-sidebar-hidden")


def flow_scenario(s):
    name = "Code Flow — Prototype · 100%"
    xdo("windowfocus", window_id(name))
    assert_desktop_icon(name)
    s.capture("15-flow-evidence", name)
    xdo("mousemove", 650, 440, "keydown", "Control_L", "click", 4, "keyup", "Control_L")
    zoomed = "Code Flow — Prototype · 115%"
    s.capture("16-flow-zoom", zoomed)
    xdo("key", "d", "s")
    s.capture("17-flow-pan", zoomed)
    click(700, 35)  # PR diff overview
    s.capture("18-flow-diff-map", name)
    click(170, 278)
    s.capture("19-flow-file-patch", "Diff · libs/dbtypes/src/document.ts")


def reopened_review(s, checkout):
    xdo("windowfocus", window_id())
    s.capture("20-restarted-dashboard")
    click(1300, 30)  # reopen the sidebar hidden in the previous session
    time.sleep(.15)
    click(478, 148)
    wait("Open diff map button", lambda: pixels(MAIN).getpixel((20, 90)) == (31, 111, 235))
    open_map()
    click(1110, 780)
    xdo("type", "What changed?")
    xdo("key", "Return")
    chats = checkout.parent / "state" / "code-flow" / "chats"
    wait("the offline Pi exchange to settle",
         lambda: any('"assistant"' in path.read_text() for path in chats.glob("*/conversation.jsonl")))
    s.capture("21-pi-chat")
    click(1270, 150)
    s.capture("22-persisted-notes")
    click(1160, 150)
    wait_for_ink("discussion disclosure arrow", (972, 251, 989, 266))
    click(979, 260)
    s.capture("23-discussion")
    click(1348, 280)
    s.capture("24-hidden-discussion")
    click(1176, 30)  # home
    time.sleep(.2)
    click(85, 30)  # mode
    xdo("key", "Home", "Return")
    click(350, 30)
    xdo("type", "fpbl")
    s.capture("25-branch-filter")
    xdo("key", "ctrl+a")
    xdo("type", "main")
    xdo("key", "Return")
    s.capture("26-branch-overview")
    # A local edit must block checkout and leave the reviewer's work intact.
    sentinel = checkout / "unfinished-review.txt"
    sentinel.write_text("Uncommitted review work")
    click(85, 96)
    s.capture("27-dirty-checkout")
    if sentinel.read_text() != "Uncommitted review work":
        raise AssertionError("Opening the map overwrote uncommitted work")


def exercise(cmd, env, run, screenshots, failure_directory=None):
    with subprocess.Popen(cmd, env=env) as app:
        try:
            run(screenshots)
        except Exception:
            if windows_named(MAIN):
                dest = failure_directory or RESULTS / MODE
                dest.mkdir(parents=True, exist_ok=True)
                pixels(MAIN).save(dest / "failure.png")
            for path in sorted(Path("/tmp/code-flow-logs").glob("*"))[-1:]:
                print(path.read_text()[-2500:], file=sys.stderr)
            raise
        finally:
            app.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                app.wait(2)
            if app.poll() is None:
                app.kill()


def main():
    (RESULTS / MODE / "failure.png").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        home = Path(directory)
        env = prepare(home)
        screenshots = Screenshots()
        legacy = MODE != "compare"
        review = ["/usr/bin/python3", "/reference/review_app.py"] if legacy else ["/build/debug/code-flow"]
        flow = ["/usr/bin/python3", "/reference/code-flow.py"] if legacy else ["/build/debug/code-flow", "--flow"]
        exercise(review, env, scenario, screenshots)
        exercise(flow, prepare_flow(env), flow_scenario, screenshots)
        exercise(review, dict(env, VISUAL_DISCUSSIONS="1"),
                 lambda s: reopened_review(s, home / "plancraft-review"), screenshots)
        screenshots.finish()
    if MODE == "compare":
        from navigation import run_navigation
        run_navigation(["/build/debug/code-flow"])


if __name__ == "__main__":
    main()
