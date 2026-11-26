"""Verify the painted border's movement and report timings from the native run."""
import csv
from itertools import groupby
import subprocess
import sys


def changes(rows, field):
    points = []
    for value, group in groupby(rows, key=lambda row: row[field]):
        points.append((int(next(group)["ms"]), float(value)))
    return points


def verify_motion(output):
    with (output / "motion.csv").open() as trace:
        phases = {name: list(rows) for name, rows in groupby(csv.DictReader(trace),
                                                             lambda row: row["phase"])}
    # Respect a Mac user's system accessibility setting even when this temporary
    # GTK fixture explicitly enables animations for the regular-motion samples.
    reduced = sys.platform == "darwin" and subprocess.check_output(
        ["osascript", "-l", "JavaScript", "-e",
         'ObjC.import("AppKit"); $.NSWorkspace.sharedWorkspace.accessibilityDisplayShouldReduceMotion'],
        text=True).strip() == "true"

    reports = []
    traces = {}
    for name, rows in phases.items():
        if name == "zoom-and-select":
            continue  # The GTK driver checks the newly selected card after the zoom.
        field = "left" if name in ("single", "repeat-reverse", "reduced") else "width"
        samples = changes(rows, field)
        if any(left < 0 for _, left in changes(rows, "left")):
            raise AssertionError(f"{name}: selected card disappeared from the camera")
        traces[name] = [v for _, v in samples]
        intervals = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
        last_input = changes(rows, "inputs")[-1][0]
        reports.append(f"{name}: {len(samples)-1} painted changes, first at "
                       f"{samples[1][0] - samples[0][0] if intervals else 0} ms, settled by "
                       f"{samples[-1][0]} ms, longest interval {max(intervals, default=0)} ms")
        if not reduced and name not in ("reduced", "zoom-reduced"):
            if len(samples) < 5:
                raise AssertionError(f"{name}: expected intermediate painted frames, saw {samples}")
            if samples[1][0] - samples[0][0] >= 120:
                raise AssertionError(f"{name}: camera remained still for at least 120 ms")
            if name in ("single", "repeat-reverse") and samples[-1][0] - last_input > 500:
                raise AssertionError(f"{name}: camera continued panning over half a second after input")
            if name.startswith("zoom") or name == "wheel-reverse":
                # Once the scale has visibly settled, replacing the preview with
                # GTK controls must keep approximately the same card dimensions.
                for i, ((t0, before), (t1, after)) in enumerate(zip(samples, samples[1:])):
                    continued = i + 2 < len(samples) and samples[i + 2][0] - t1 < 120
                    if t1 - t0 > 150 and not continued and abs(after - before) > max(6, before * .05):
                        raise AssertionError(f"{name}: card jumped {before} → {after} "
                                             f"after {t1 - t0} ms at rest")

    single = traces["single"]
    page = float(phases["single"][0]["page"])
    distance = .4 * page
    assert abs(single[0] - single[-1] - distance) <= 2, "Wrong Shift+Right destination"
    assert all(a >= b for a, b in zip(single, single[1:])), "Single pan reversed or overshot"
    rapid = traces["repeat-reverse"]
    assert max(rapid) - rapid[0] > distance, "Second key did not extend the motion"
    assert abs(rapid[-1] - rapid[0]) <= 2, "Reversing the pan failed to return to the same card position"
    instant = traces["reduced"]
    assert len(instant) == 2 and abs(instant[-1] - instant[0] - distance) <= 2, (
        "Disabling animations must move to the destination in one painted step")
    if reduced:
        assert len(single) == 2, "macOS Reduce Motion must override the GTK animation setting"
    zoom_out, gesture, reset = (traces[name] for name in ("zoom-out", "wheel-reverse", "zoom-reset"))
    assert zoom_out[-1] < zoom_out[0] * .92, "Zoom-out button did not shrink the selected card"
    assert min(gesture) < gesture[0] * .90, "Wheel zoom did not shrink the card"
    wheel = phases["wheel-reverse"]
    reverse_sent = changes(wheel, "inputs")[-1][0]
    live = [float(r["width"]) for r in wheel if reverse_sent <= int(r["ms"]) <= reverse_sent + 200]
    if not reduced:
        assert live and live[-1] > min(float(r["width"]) for r in wheel if int(r["ms"]) <= reverse_sent) + 2, (
            "The opposite wheel input failed to reverse the painted scale during the gesture")
    assert abs(reset[-1] - zoom_out[0]) <= 3, "100% did not restore the original painted width"
    assert len(traces["zoom-reduced"]) == 2, "Reduced-motion zoom must present a single change of scale"
    for name, pivot_fraction in [("zoom-out", .5), ("wheel-reverse", .55), ("zoom-reset", .5)]:
        rows = phases[name]
        pivot = pivot_fraction * float(rows[0]["page"]) - 1  # the capture excludes a 1pt margin
        start = rows[0]
        fraction = (pivot - float(start["left"])) / float(start["width"])
        drift = max(abs(float(r["left"]) + fraction * float(r["width"]) - pivot) for r in rows)
        assert drift <= 6, f"{name}: the selected location drifted {drift:.1f} points from its pivot"
    report = "\n".join(reports)
    (output / "motion.txt").write_text(report + "\n")
    print(report)
