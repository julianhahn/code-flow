# Code Flow

A small Linux and macOS GUI for reviewing changes and opening source locations in Zed.

## macOS setup

The review window needs Homebrew Python and GTK 3. Install them, create a separate
review clone, then install the Finder launcher from this repository:

```sh
brew install gtk+3 pygobject3
# Only if ~/plancraft-review does not already exist:
gh repo clone plancraft/plancraft ~/plancraft-review -- --filter=blob:none
bash install-macos.sh
open "$HOME/Applications/Code Flow.app"
```

The installer creates `~/Applications/Code Flow.app`. Keep this repository in place;
the launcher runs its `review_app.py`. Remove the app to uninstall the launcher.
It preserves the install-time command search path so Finder can find `gh` and `pi`.
Both tools need an existing login. No credentials are stored in the launcher.

Run tests with Homebrew's `python3 -m unittest discover -v`.
The separate Flow mode still needs a current tokensave index in `~/plancraft`;
reviewing PRs does not.

## Remaining setup work

- Linux/Pop!_OS: the dock still does not reliably show the app icon. Match the
  running window identity to the desktop launcher and verify on the real desktop.
- The Linux launcher and desktop-entry installation remain machine-local.
- The macOS launcher uses the default application icon.
- Zoom batches wheel events; it is not continuous Excalidraw-style zoom.

## Standalone review app

Run `code-flow` from any folder, or open **Code Flow** from the Linux app menu.
The installed launcher is `/home/julian/.local/bin/code-flow`; the desktop entry
is `/home/julian/.local/share/applications/code-flow.desktop`.
Remove those two files to uninstall the shortcuts; the repositories remain.

The review app uses only `~/plancraft-review`, a separate clone.
Choose a branch and comparison base, or enter a PR number. Refresh fetches branches.
PR loading fetches its actual base and head. Comparisons use pinned commit SHAs
and their merge base. Dirty checkouts and active Git operations block switching.
The main Plancraft checkout is never switched. No tokensave index is needed.

Selecting a branch or PR opens its compact overview. Click **Open diff map**
to load all patches as open cards, with no per-file scrollbar or expanders.
Ctrl/Cmd + scroll (mouse or trackpad) or the +/− buttons zoom.
Two-finger scrolling pans in both directions; middle-drag also pans.
Pinch zoom is available when the GTK backend delivers pinch gestures.
Shared folder nodes
branch into filenames so prefixes are not repeated. Hover for the full path;
click the copy icon to copy it. Columns remain DBTypes → Frontend → Shared/API
→ Backend → Other. Added files are green, deleted red, modified yellow;
renames use yellow and show both paths on hover. Added/deleted lines have
separate green/red backgrounds. Unchanged lines remain neutral.

Current limits: no review comments, side-by-side view, or PR URL input.
Very large patches and zooming large maps can still be slow; all cards are
rendered rather than virtualized.

### Import and reference links

Opening a diff map automatically runs this sequence:

1. **Checkout** — fetch and compare commits, then load the reviewed commit.
2. **Install dependencies** — always run `pnpm install --frozen-lockfile` in the review clone, even if dependencies already exist.
3. **Read changes** — load file patches and commit markers.
4. **Find links** — analyse imports and references, or validate and reuse cached results.
5. **Draw map** — create file cards in small batches so the window stays responsive.

A progress bar shows the current step. Live details show install output, the
project being analysed, and file counts. The bar tracks completed steps, not an
estimated waiting time. There are no install or analysis buttons.

Opening a map can download packages and run repository/dependency install scripts
without another prompt. Review trusted code only. The main checkout is never used.
Node and pnpm must be available to terminal and desktop launches on both macOS and
Linux. The repository must declare its TypeScript dependency. The link worker
reads the clone's `tsconfig` files; it does not run builds or Nx itself.

**Cancel build** stops the pipeline. Cancellation may leave a partial dependency
installation. A failed install stops the build at that step. A link-analysis
failure still allows the diff map to open, with a visible notice. Reopen the map
to retry. Branch switching and chat are locked until the build stops or finishes.

The **Imports** and **References** toggles only control visibility. Both start off;
the link data is already prepared.

- Click a filename or its diff text to select a file. Its direct incoming and
  outgoing neighbours stay visible. Unrelated cards and folder branches fade.
- Imports use blue dashed arrows. References use purple dotted arrows. Arrows
  point from the using file to the defining file, not in runtime execution order.
- Column and file order stay unchanged. Clear focus with Escape, **Clear focus**,
  or a click on empty map space. Text selection for chat stays separate.
- **Show all links** shows connections without a selection. With a selection,
  unrelated arrows remain faint. The first 500 arrows are drawn; focused links
  take priority. **Connections** retains the full evidence list.
- Click an arrow or **Connections** to inspect source locations. **Focus symbol**
  narrows the highlight to one symbol. **Open use** and **Open definition** open
  the exact location in Zed after checking the reviewed commit is still loaded.
- Unchanged linked files remain in the evidence list, marked “outside map”. They
  are not inserted into the layout. Deleted files have no head-version links.

Analysis runs in a cancellable child process with a 110-second worker limit and
8 GiB JavaScript heap limit. Large repositories may reach these limits. The map
remains available on failure. Cached results are checked against compiler code,
source/configuration reads, directory scans, and dependency resolution checks.
Dirty clones and stale commit results are rejected.

Scope: tracked JS/TS files covered by the loaded projects, including unchanged
callers. External package internals, non-literal dynamic imports, runtime dispatch,
and base-commit links are not mapped. Unresolved references and incomplete project
coverage are reported in **Connections**. Analysis stops collecting at 150,000
source locations and explicitly marks that result incomplete.

Regression tests need GTK and a display on either OS. For the real compiler tests,
point `CODE_FLOW_TEST_TYPESCRIPT` at an already installed TypeScript package directory
(with `lib/typescript.js` inside). The tests create temporary repositories; they
never install packages or modify the source checkout:

```sh
CODE_FLOW_TEST_TYPESCRIPT=/path/to/typescript python3 -m unittest -v test_review_build test_dependency_analysis test_dependency_overlay test_diff_canvas test_review_app
```

**Flow mode** launches the existing flow viewer unchanged. Its own index and
source-evidence requirements still apply; stale examples may not render.

Checks: `/usr/bin/python3 -m unittest -v test_diff_canvas test_review_app test_review_summary test_review_git test_flow_model`.

## Ask Pi

The right-hand chat panel keeps one conversation per PR. Open the diff map first,
select code if needed, then ask a question. Each message captures the PR number,
reviewed commit, focused filename, file diff, and selected patch text. The context
label shows what will be attached. Large diffs/selections are explicitly truncated.

Pi uses its configured default model and login through RPC. Only `read`, `grep`,
`find`, and `ls` are enabled. Extension, skill, prompt-template, project-settings,
and context-file loading are disabled. There is no shell, edit, write, or GitHub
posting tool. This is a tool restriction, not an OS filesystem sandbox: read tools
are instructed to stay inside the review checkout.

While Pi answers, branch-switch controls are locked. The checkout HEAD is checked
before and after the answer; a mismatch is reported as incomplete. Other app
instances can still change that checkout, so avoid reviewing two branches at once.
Use Stop to end an answer. Each request is limited to 110 seconds.

Chat sessions and displayed history live in `~/.local/state/code-flow/chats/`
(or `$XDG_STATE_HOME/code-flow/chats/`). Old messages retain their commit labels.
A session lock prevents two windows from writing the same PR conversation at once.
Pi reads further files from the checkout; the system prompt is review-specific,
not the full global interactive Pi setup.

Chat tests: `/usr/bin/python3 -m unittest -v test_pi_chat test_review_app`.

## Run original flow viewer

```sh
/usr/bin/python3 code-flow.py
```

Requires GTK 3, Python GObject bindings, Pycairo, and Zed. On Debian/Ubuntu, the packages are `python3-gi`, `gir1.2-gtk-3.0`, and `python3-cairo`.

This prototype reads the saved tokensave index for `main` in `/home/julian/plancraft`. Set `CODE_FLOW_ROOT` to use another checkout. It never writes to that index or the source checkout.

Agents now author JSON flows, not Python. The renderer validates evidence and calculates positions. See [FLOW-FORMAT.md](FLOW-FORMAT.md) for the agent contract, custom queue/HTTP links, and validation command. Use **Open flow JSON** to load a flow.

Run the model checks with `python3 -m unittest -v test_flow_model`.

## Controls

- Ctrl + mouse wheel: zoom.
- Hold middle mouse button and drag: pan.
- WASD: pan when the canvas has focus.
- Click a blue source path: open the exact line in Zed.
- Find function: search the saved tokensave index.
- PR diff overview: show changed files in four review columns: DBTypes → Frontend → Shared/API → Backend.
- Click a changed-file card to read its Git diff in the GUI. Set `CODE_FLOW_DIFF_BASE` to change the comparison base.
- Publish example: open the source-verified `publishDocumentRequest` flow.

## Reading the flow

- Amber IF: condition, with YES/NO paths.
- Blue CALL: function call.
- Red THROW: stop with an error.
- Green RETURN: finish the request.

The visual convention is right for sequence, down for a function drill-down, and dashed up for a return. Branch lanes do not imply a deeper function call.

## PR diff overview

The diff view is a separate view from flow mode. It does not change the flow JSON format or its layout. It reads `git diff --name-status` and groups paths by simple path hints. Unknown paths go into `Shared/API`. The diff window shows the real patch, and the source card can still be opened in Zed later.

This is an overview, not a complete GitHub replacement yet. It does not show inline comments, review threads, or side-by-side hunks. The comparison defaults to `origin/main`; use `CODE_FLOW_DIFF_BASE` for another base.

## Current limits

The publication example in `flows/publish-request.json` is manually checked against its source, not automatically inferred from graph edges. Source hashes prevent showing it after its evidence changes. `publish-request-flow.py` is the old, unused prototype definition; retained only for comparison. It covers one function body, not the internals of every called function.

Search results open source files; arbitrary verified execution flows are not implemented. Tokensave call order alone does not prove runtime order. Free-text workflow search is not implemented.

This is a prototype, not production software. It contains a Plancraft-specific example and stays private.
