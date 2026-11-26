# Code Flow

Code Flow is a native Rust and GTK 3 desktop app for reviewing Git branches and pull requests on macOS and Linux. Open a review to see its overview, then choose **Open diff map** to explore the patches. The source-evidence flow viewer is available separately.

## Run

Install a current stable Rust toolchain, Git, and the GitHub CLI. The default review repository is `plancraft/plancraft`. Its dedicated local clone lives at `~/plancraft-review`. If that clone already exists, reuse it.

```sh
# macOS with Homebrew
brew install gtk+3 pkg-config gh

# Debian / Ubuntu / Pop!_OS
sudo apt install build-essential pkg-config libgtk-3-dev git gh

# One-time setup for a new review checkout
gh auth login
gh repo clone plancraft/plancraft "$HOME/plancraft-review" -- --filter=blob:none

# Run from this source checkout
cargo run --locked --release
```

On macOS, install **Code Flow** in your Applications folder and open it from Finder or the Dock.

```sh
./install-macos.sh
open "$HOME/Applications/Code Flow.app"
```

On Linux, run `./install-linux.sh` and choose **Code Flow** in the application menu. The installer adds `code-flow` to `~/.local/bin` and installs a desktop entry and icon. Both installers copy a release binary and capture the installing shell’s command search path and Pi settings directory. Install Zed’s `zed` command for source links and sign in to `pi` to use Ask Pi.

Use **Light mode** in the upper-right corner of either window to switch the appearance. Dark is the default. The review map and source-evidence viewer share the saved preference.

## Review map

- Type part of a branch name, select a recent review, or enter a PR number. **↻** fetches branches. Branches without a PR have an editable comparison base.
- Pan with two-finger scrolling or the middle mouse button. A drag updates the horizontal and vertical scroll offsets together on the GTK frame clock. Card layout and text buffers stay in place during panning.
- Use Ctrl/Cmd + scroll or pinch to zoom around the pointer or gesture center. The zoom controls use the middle of the viewport. **Fit all** shows the board at a reduced scale. Double-click diff text to select a word. Hold **Ctrl** and double-click a file title or its diff to center the card at a minimum of 100%.
- Left and Right move one populated column at a time and keep your place vertically. Up and Down step through files in the current column. The same arrows select the same files after zooming or panning. Shift + arrow glides by 40% of the viewport while keeping the selected file. Repeated presses adjust the destination during the glide. The app honors the desktop’s reduced-motion setting. **V** or the **Viewed** checkbox marks the file and selects the next unviewed file below it in that column. Selection stays on the file when you unmark it or reach the last unviewed file. Markers survive unrelated commits and expire when the file changes.
- **Hide specs** filters test cards from the map and its search and progress counts. Ctrl/Cmd + F searches displayed diffs. Select patch text to attach that exact selection to a chat.
- The **Files** tab jumps to files. **Conversations** reads GitHub comments, reviews and thread replies. Hide and Unhide affect the local view. **Notes** saves formatted text per PR across commits.
- Ctrl/Cmd + B toggles the sidebar. Press **?** for shortcuts. Code Flow remembers sidebar visibility and recently opened reviews.

The review build checks for local changes and in-progress Git operations before switching the dedicated clone. For PRs it fetches the head and base, computes their merge base and reads each patch from those commits. The GTK window reports the current build step and supports cancellation.

## Ask Pi and local state

Each PR has a separate chat. Open its map before asking a question. Each message records the reviewed commit, file, patch and selected rows. The panel displays the configured model and effort, then the model that answered. Read-only tool access includes `read`, `grep`, `find` and `ls`. Pi reads the local checkout under your account’s filesystem permissions.

Code Flow reuses the existing notes, viewed markers, hidden discussions, recent selections and chat history under `$XDG_STATE_HOME/code-flow`, defaulting to `~/.local/state/code-flow`. A conversation lock protects each Pi session, and the checkout HEAD is checked before and after a response. Stop cancels a request. The deadline is 110 seconds. A per-session log under the system temporary directory’s `code-flow-logs` folder captures errors from desktop launches.

The defaults work with the original installation. For another repository, set `CODE_FLOW_REPO` to its GitHub owner/name, `CODE_FLOW_REVIEW_ROOT` to a separate clone, and `CODE_FLOW_PROTECTED_ROOT` to the main source checkout. Set `PI_CODING_AGENT_DIR` before installing a desktop launcher when using a custom Pi profile.

## Source-evidence flow viewer

```sh
cargo run --locked -- --flow
cargo run --locked -- --flow path/to/flow.json --root "$HOME/plancraft"
cargo run --locked -- --validate-flow path/to/flow.json --root "$HOME/plancraft"
```

The viewer supports both JSON formats, verifies file hashes and line numbers, and lays out sequence, branches, calls, returns and runtime handoffs. Uncertain links have visible warnings. The bundled example describes the source revision against which it was authored. A changed or missing source produces an explanation in the window. **Find function** reads the saved tokensave index for main. **PR diff overview** compares the source checkout with `CODE_FLOW_DIFF_BASE` (default `origin/main`). Middle-drag or WASD pans the diagram. Ctrl + scroll zooms. Full source paths open the indicated line in Zed. See [the flow format](FLOW-FORMAT.md).

## Verification

```sh
cargo test --all-targets --locked
cargo clippy --all-targets --locked -- -D warnings
tests/visual/run.sh
```

The visual suite drives the shipping executable against a real temporary Git clone and offline GitHub and Pi fixtures. Its full-window screenshots come from the previous release at `0d6518bb2a`. It tests intermediate positions during a drag as well as the final image. The same Docker command runs on Linux and macOS. See [visual regression tests](tests/visual/README.md). The [native desktop comparison](tests/visual/native/README.md) also captures the original and Rust windows on the same display and measures painted frame intervals during a sustained drag over 160 files.

## Decisions carried forward from earlier iterations

- Selection follows an explicit click or arrow key. Horizontal navigation considers card height in the neighboring column so wide or off-screen cards remain reachable. The sidebar preference and first-run shortcut help survive restarts. [PR 3](https://github.com/julianhahn/code-flow/pull/3) and [PR 4](https://github.com/julianhahn/code-flow/pull/4)
- Zooming out is part of tracking what remains to review. Ctrl + double-click restores a readable scale, and centering a tall card keeps its header visible. [PR 2](https://github.com/julianhahn/code-flow/pull/2)
- A stale local base can introduce unrelated changes. The rewrite incorporates the open PR’s pinned GitHub base and retarget checks, including a regression with a moving branch. [PR 1](https://github.com/julianhahn/code-flow/pull/1)
- Building a review reads Git snapshots directly. Earlier dependency installation and TypeScript import analysis were removed from the build path. Reference mode retains the existing empty-state behavior. [Dependency removal](https://github.com/julianhahn/code-flow/commit/f667d557bf) and [import analysis change](https://github.com/julianhahn/code-flow/commit/9d7949387f)
- The global theme covers dialogs, popovers and menus as well as the review window. The shared widget helpers check style names against the stylesheet, and tests check text contrast. [Theme and selection decisions](https://github.com/julianhahn/code-flow/pull/4)
- Card controls and borders scale with zoom. The canvas measures actual GTK text layout to keep the next column clear of long wrapped patches. [Card allocation fixes](https://github.com/julianhahn/code-flow/commit/50bf5ae047)
- Reopened Pi sessions reread the user’s configured provider, model and per-model thinking level. [Pi settings change](https://github.com/julianhahn/code-flow/commit/d705b78a35)
