# Visual regression

Run `tests/visual/run.sh` from a checkout with Docker running. On macOS and Linux the runner builds the Rust executable, starts a private 96-DPI X11 display, and drives the production window with mouse and keyboard input. The capture container has networking disabled. A disposable home contains a separate real Git clone and an offline GitHub fixture for PR #42. GitHub metadata, discussions and Pi replies come from deterministic fixture commands.

The PNGs in `goldens/` came from the Python/GTK app at commit `0d6518bb2a6ea4fd715c84f253a8c6529f987b81`. The suite checks the entire window for each interaction. At four intermediate positions during a held middle drag, the suite locates the selected card's painted blue border and checks that it follows the pointer on both axes. A single differing pixel fails the suite. The two 115% flow-viewer captures allow one RGB level of rounding from bilinear interpolation. The suite also fails on an unexpected window size, missing golden or missing scenario.

Actual screenshots and red difference overlays are written under `target/visual/compare/`. Running `tests/visual/run.sh check-legacy` replays the same scenario against the immutable previous version and verifies the reference images. `tests/visual/run.sh record-legacy` can regenerate goldens only from that Git revision.

The suite covers first-run shortcuts, dashboard, PR overview, diff map, two-axis middle drag, fit all, hidden specs, arrow-key selection, viewed cards, search, file list, saved notes, conversations, the hidden sidebar, reopened review state, a Pi RPC exchange, expanded and hidden conversations, fuzzy branch selection, branch-only overview and dirty-checkout errors. The standalone evidence viewer is checked at its initial zoom, after wheel zoom, after WASD pan, in diff overview mode and with a file patch open.

For a second comparison on the native macOS Quartz backend, see the [macOS visual test](native/README.md).


## Map navigation and Viewed

Run `tests/visual/run.sh navigation` for the focused desktop test. The default comparison also runs it. It builds a temporary Git review with 123 aliased files in four populated columns and 26 saved Viewed markers. The fixture preserves folder depth, card widths, patch heights and file ordering from the reported review.

The test sends native X11 mouse and key events. After each step it reads the displayed filename and full breadcrumb from GTK accessibility, finds that card’s screen bounds, and checks the blue selection border in the window pixels. It follows and reverses the same row across all four columns at Fit all, at 100% after double-clicking, and after a two-axis drag. It checks the four outer boundaries and the camera position when an arrow has no neighbor.

Viewed cases cover keyboard V, the sticky control and a different card’s checkbox. They verify skipped viewed or filtered files, staying on the final file, unmarking, and quick successive V and Shift+Left inputs. Per-step captures and failures are written under `target/visual/navigation/`. Expected file identities come from the fixture. Golden recording continues to use the immutable pre-rewrite revision.
