# Native desktop regression

Run this comparison on a desktop with GTK 3, its development headers, a C compiler, pkg-config, Rust, Git and Python with PyGObject installed. Both implementations use the same native theme, fonts, DPI and display. Keep the display awake while the two test windows open and close.

Run on macOS with Homebrew GTK and PyGObject.

```sh
caffeinate -d -u "$(brew --prefix)/bin/python3" tests/visual/native/run.py --stress
```

Run on Linux with the GTK development packages and PyGObject.

```sh
/usr/bin/python3 tests/visual/native/run.py --stress
```

Linux CI can use `xvfb-run -a -s '-screen 0 1600x1200x24'` before the Python command. The macOS run uses Quartz. Run from the repository root and keep other windows away from the test window.

The runner builds the shipping Rust binary and extracts the Python reference app from Git revision `0d6518bb2a6ea4fd715c84f253a8c6529f987b81`. Each app gets a temporary home directory, separate Git clone and offline PR fixture. The GTK test module activates the ordinary buttons and emits middle-button and motion events within each process. It checks both camera coordinates while the button is held. The driver reads the window pixels through GDK after painting. This exercises native window rendering and GTK input handlers without moving the system pointer or requiring desktop accessibility permissions.

The comparison covers the dashboard, PR overview, diff map, two-axis pan, Fit all, Hide specs, and Viewed. Each screenshot can differ by at most 40 pixels with any RGB channel changing by more than three levels. The runner saves both sets of PNGs and any difference masks under `target/visual/native/<platform>/`.

The native suite also opens an offline replica of the four-column layout from PR #28118. It sends Left and Right through all four columns at Fit all and 100%, checks the selected file after each key, and presses again at both outside edges. It unmarks the first Frontend file, then marks it again using the Viewed control and checks that selection skips the already-viewed second file. The suite saves a screenshot at each zoom and captures the window if an unexpected file is selected.

`--stress` also opens a 160-file review in the Rust app, sends middle-drag motion events for two seconds and samples real canvas pixels during the gesture. Its report includes distinct painted updates, median and 95th-percentile intervals and the longest gap. The test fails if it observes fewer than 30 painted updates or the selected card changes or is rebuilt. These timings describe the local display and capture load. The input events are synthetic.
