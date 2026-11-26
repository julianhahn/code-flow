# Visual regression

Run `tests/visual/run.sh` from a checkout with Docker running. On macOS and Linux the runner builds the Rust executable, starts a private 96-DPI X11 display, and drives the production window with mouse and keyboard input. The capture container has networking disabled. A disposable home contains a separate real Git clone and an offline GitHub fixture for PR #42. Your checkouts, login and review state are never used.

The PNGs in `goldens/` came from the Python/GTK app at commit `0d6518bb2a6ea4fd715c84f253a8c6529f987b81`. The suite checks the entire window for each interaction. It also verifies that middle drag changes the map independently of the goldens. A pixel differs if any RGB channel changes by more than 3 levels. More than 40 differing pixels, an unexpected window size, a missing golden or a missing scenario fails the suite.

Actual screenshots and red difference overlays are written under `target/visual/compare/`. Running `tests/visual/run.sh check-legacy` replays the same scenario against the immutable previous version and verifies the reference images. `tests/visual/run.sh record-legacy` can regenerate goldens only from that Git revision.

The suite covers first-run shortcuts, dashboard, PR overview, diff map, two-axis middle drag, fit all, hidden specs, arrow-key selection, viewed cards, search, file list, saved notes, conversations, and the hidden sidebar. The fixtures do not contact GitHub, Pi or Zed.
