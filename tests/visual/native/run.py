"""Compare two locally rendered GTK windows using the same native backend and display."""
import argparse
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from fixture import prepare
from trajectory import verify_motion
from pr_28118 import FIRST_PATHS, LANES, prepare_pr_28118

BASELINE = "0d6518bb2a6ea4fd715c84f253a8c6529f987b81"
STATES = ["dashboard", "overview", "map", "pan", "fit", "hide-specs", "viewed"]


def archive_legacy(destination):
    destination.mkdir()
    with subprocess.Popen(["git", "-C", str(ROOT), "archive", BASELINE],
                          stdout=subprocess.PIPE) as process:
        subprocess.run(["tar", "-xf", "-", "-C", str(destination)], stdin=process.stdout, check=True)
        if process.wait() != 0:
            raise RuntimeError(f"Cannot read reference revision {BASELINE}")


def compile_module(destination):
    flags = subprocess.check_output(["pkg-config", "--cflags", "--libs", "gtk+-3.0"],
                                    text=True).split()
    link = ["-dynamiclib"] if sys.platform == "darwin" else ["-fPIC", "-shared"]
    subprocess.run(["cc", "-O2", *link, "-o", str(destination), str(HERE / "driver.c"),
                    str(HERE / "motion.c"), *flags],
                   check=True)


def extend_review(home, env):
    source = home / "source"

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=source, env=env,
                                       stderr=subprocess.PIPE, text=True).strip()

    git("switch", "feature/publishing")
    folders = ["libs/dbtypes", "apps/docs-frontend", "pkgs/docs-shared", "pkgs/docs-worker", "tools"]
    for index in range(153):
        path = source / folders[index % len(folders)] / "src" / f"feature-{index:03}" / "review.rs"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f'fn review_{n}() {{ let revision = "{index}:{n}"; }}\n'
                                for n in range(20 + index % 5)))
    git("add", ".")
    git("commit", "-m", "Build a 160-file map")
    head = git("rev-parse", "HEAD")
    git("update-ref", "refs/pull/42/head", head)
    git("switch", "main")
    metadata = home / "pr.json"
    data = json.loads(metadata.read_text())
    data.update(headRefOid=head, changedFiles=160)
    metadata.write_text(json.dumps(data))


def run(binary, home, module, output, stress=False, navigation=False, motion=False):
    home.mkdir()
    env = prepare_pr_28118(home) if navigation else prepare(home)
    (home / "state/code-flow/help-seen").touch()
    if stress:
        extend_review(home, env)
    output.mkdir(parents=True, exist_ok=True)
    done = output / "complete"
    done.unlink(missing_ok=True)
    (output / "error").unlink(missing_ok=True)
    (output / "unexpected-selection.png").unlink(missing_ok=True)
    env.update(GTK_MODULES=str(module), NATIVE_CAPTURES=str(output),
               NATIVE_STRESS="1" if stress else "", GTK_OVERLAY_SCROLLING="0",
               CODE_FLOW_REVIEW_ROOT=str(home / "plancraft-review"), CODE_FLOW_REPO="plancraft/plancraft")
    if output.name == "rust":
        env["NATIVE_APPEARANCE"] = "1"
    if motion:
        for stale_frame in output.glob("*.missing.png"):
            stale_frame.unlink()
        for filename in ("motion.csv", "motion.txt", "motion-failed.png"):
            (output / filename).unlink(missing_ok=True)
        env["NATIVE_MOTION"] = "1"
        env["NATIVE_NEXT_BACKEND"] = LANES["Backend"][1]
    if navigation:
        env["NATIVE_NAVIGATION"] = "\n".join(FIRST_PATHS)
        env["NATIVE_NEXT_UNVIEWED"] = LANES["Frontend"][2]
    if sys.platform == "darwin":
        env["GDK_BACKEND"] = "quartz"
    process = subprocess.run(binary, env=env, timeout=90)
    if process.returncode or not done.exists():
        failure = output / "error"
        detail = failure.read_text() if failure.exists() else "GTK driver did not finish"
        raise RuntimeError(f"{output.name} failed ({process.returncode}): {detail}")


def compare(old_file, new_file, difference, control, rect):
    a, b = (GdkPixbuf.Pixbuf.new_from_file(str(path)) for path in (old_file, new_file))
    size = (a.get_width(), a.get_height())
    if size != (b.get_width(), b.get_height()) or a.get_n_channels() != b.get_n_channels():
        raise ValueError(f"Inconsistent window format in {new_file.name}")
    # The new control occupies the same formerly empty spot in each review state.
    x, y, w, h = rect
    control.copy_area(x, y, w, h, a, x, y)
    old, new = a.get_pixels(), b.get_pixels()
    if old == new:
        difference.unlink(missing_ok=True)
        return size, 0

    # Equality above makes identical captures cheap, including Retina-sized PNGs.
    width, height = size
    channels = a.get_n_channels()
    count = 0
    mask = bytearray(width * height * 3)
    for y in range(height):
        sa, sb = y * a.get_rowstride(), y * b.get_rowstride()
        if old[sa:sa+width*channels] == new[sb:sb+width*channels]:
            continue
        for x in range(width):
            oa, ob = sa + channels*x, sb + channels*x
            if any(abs(old[oa+c] - new[ob+c]) > 3 for c in range(3)):
                i = 3 * (y * width + x)
                mask[i:i+3] = b'\xff\x41\x5b'
                count += 1
    if count:
        image = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(mask), GdkPixbuf.Colorspace.RGB,
                                                False, 8, width, height, width * 3)
        image.savev(str(difference), "png", [], [])
    else:
        difference.unlink(missing_ok=True)
    return size, count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stress", action="store_true", help="also profile a 160-file two-second drag")
    parser.add_argument("--motion-only", action="store_true", help="profile animated keyboard pans and zoom only")
    args = parser.parse_args()
    output = ROOT / "target" / "visual" / "native" / sys.platform
    subprocess.run(["cargo", "build", "--locked", "--bin", "code-flow"], cwd=ROOT, check=True)
    metadata = json.loads(subprocess.check_output(["cargo", "metadata", "--no-deps", "--format-version", "1"],
                                                  cwd=ROOT, text=True))
    candidate = [str(Path(metadata["target_directory"]) / "debug/code-flow")]
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        module = work / "native-capture.so"
        compile_module(module)
        run(candidate, work / "home-motion", module, output / "motion", navigation=True, motion=True)
        verify_motion(output / "motion")
        if args.motion_only:
            return
        archive_legacy(work / "legacy")
        for name, binary in [("legacy", [sys.executable, str(work / "legacy/review_app.py")]),
                             ("rust", candidate)]:
            run(binary, work / ("home-" + name), module, output / name)
        load = lambda name: GdkPixbuf.Pixbuf.new_from_file(str(output / "rust" / name))
        control = load("dashboard.png")
        rect = tuple(map(int, (output / "rust/appearance-rect").read_text().split()))
        for filename, colors in [("map-light.png", ["ffffff", "e0f5e5", "ffe6e6"]),
                                 ("map.png", ["0d1117", "12261e", "2d1519"])]:
            data = load(filename).get_pixels()
            for rgb in colors:
                assert data.count(bytes.fromhex(rgb)) > 100, f"{filename} missing {rgb}"
        failures = []
        for state in STATES:
            ref = output / "legacy" / (state + ".png")
            actual = output / "rust" / (state + ".png")
            size, count = compare(ref, actual, output / (state + ".diff.png"), control, rect)
            print(f"{state}: {size[0]}×{size[1]}, {count} changed pixels")
            if count > 40:
                failures.append(state)
        if failures:
            raise SystemExit(f"Native visual differences in {', '.join(failures)}. See {output}")
        run(candidate, work / "home-navigation", module, output / "navigation", navigation=True)
        print("PR #28118 arrow navigation and Viewed selection passed.")
        if args.stress:
            run(candidate, work / "home-stress", module, output / "stress", stress=True)
            print((output / "stress/pan.txt").read_text())


if __name__ == "__main__":
    main()
