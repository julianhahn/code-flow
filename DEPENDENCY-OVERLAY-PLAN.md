# Dependency overlay plan

Status: implemented. The first-version boundary below is unchanged.

## Validation

- macOS: all 104 repository tests pass.
- Linux: all 67 build, dependency, map, and review-window tests pass in an Alpine container with a virtual display.
- Real TypeScript 6.0.3 fixtures cover aliases, re-exports, type references, project references, conditional package exports, cache invalidation, and unchanged callers.
- The rendered map was checked for fixed file order, separate arrow styles, and selection fading.
- The full Linux suite needs a local Pi settings file for an unrelated chat-configuration test. No credentials were copied into the test container.
- PR 25016 at `c46d2fd960d31f3982798b0c7ff62fcb426f4a94`: analysis with an 8 GiB heap returned 59,268 file links in 100.72 seconds without a memory crash. Coverage is partial: the existing 150,000-source-location limit stopped collection before all projects were analysed.
- A real Linux desktop launch remains unverified. The real analysis used dependencies already installed by the app in the review clone; the agent did not run an installation.

Implementation: `ReviewBuild.py` runs the automatic pipeline; `ReviewBuildProgress.py` shows its steps. `DependencyAnalysis.py` manages child processes and the cache; `dependency_worker.cjs` reads TypeScript links; `DependencyGraph.py` handles focus and routes; `DependencyOverlay.py` draws and controls visibility. `diff_canvas.py` and `review_app.py` connect these parts to the map.

Usage and limits are documented in `README.md`.

## Problem

The review map shows folders and changed files. It does not show which files import or use each other. Reviewers need those links without losing the existing map order.

## Evidence

- `diff_canvas.py` places cards in this order: DBTypes → Frontend → Shared/API → Backend → Other.
- Its `render()` method records card positions and draws folder connections.
- Reviews use a separate clone. The main source checkout must remain untouched.
- Nx mainly provides project-level dependencies. TypeScript can resolve file imports and references to symbols such as functions, types, and variables.

## Decision

Add an optional connection layer to the existing map. Use the reviewed repository's TypeScript language service. Do not add Nx in the first version.

Keep column, folder, and file order unchanged. Links describe static code dependencies, not runtime execution order.

Julian's decision: opening a diff map always runs checkout → dependency installation → reading changes → link analysis → map drawing. Installation and analysis are automatic steps, not buttons. A progress bar shows the current step and live details. The Imports and References toggles only change visibility.

### Selection and appearance

- Add independent `Imports` and `References` toggles. Both start off.
- Keep existing folder lines unchanged.
- Draw imports as blue dashed arrows and references as purple dotted arrows. Include a legend.
- Arrow direction is using file → defining file.
- Selecting a file keeps it and its direct neighbours fully visible. Give the selected file a strong outline.
- Highlight both incoming and outgoing links: what this file uses and what uses this file.
- Fade unrelated cards, folder branches, and connections. Keep them readable and clickable.
- Keep ancestor folders of highlighted cards readable so paths remain clear.
- Only enabled link types affect the highlight.
- Do not follow indirect links by default.
- Clicking empty space or pressing Escape clears selection and restores normal visibility.
- With no selection, hide connection arrows unless `Show all links` is enabled.
- When both link toggles are off, keep the current map behaviour.

Julian's decision: selection should highlight linked and referenced items and fade unrelated items. This reduces visual clutter without rearranging the map.

## Implementation steps

### 1. Prepare the review clone

Why: TypeScript needs the repository's dependencies and configuration to resolve links correctly.

- Use only the separate review clone, never the main checkout or a linked worktree.
- Always run `pnpm install --frozen-lockfile` after checkout, even when dependencies and a cached graph exist.
- Do not show an install button or confirmation dialog. State that opening a map can run repository and dependency install scripts.
- Do not change package manifests or lockfiles.
- Find Node and pnpm through the configured command search path on macOS and Linux.
- Carry that search path into desktop launches, as for terminal launches.
- Show setup progress and allow cancellation. If installation fails, stop at that step and show the error.

### 2. Build a source-backed link map

Why: highlights need real import and symbol targets, not filename guesses.

- Add a small Node worker using the review clone's installed TypeScript version.
- Read the relevant `tsconfig` files, path aliases, and project references.
- Resolve imports, type-only imports, and re-exports.
- Record symbol uses separately from import declarations so an import alone does not count as a reference.
- Search the relevant workspace projects for incoming uses, including unchanged files. Searching changed files alone would miss callers.
- Return file paths, symbol names, link kinds, and source locations as JSON.
- Deduplicate repeated evidence. Mark unresolved or unsupported cases instead of inventing links.
- Label coverage clearly. Missing projects or dependencies must not look like a complete result.
- Analyse the reviewed head commit first. Deleted files have no head-version links; label them as unavailable rather than having no references.

### 3. Draw links without changing layout

Why: the new connections must stay separate from the folder structure.

- Store dependency links separately from the existing folder tree.
- Attach arrows to file-card boundaries using the recorded card positions.
- Route arrows through available gaps and avoid covering code where possible.
- Combine repeated links of the same kind between two files into one arrow with a count.
- Keep drawing and hit detection aligned during zoom, pan, and map rebuilds.
- Clicking an arrow opens its evidence list with source locations.
- Show linked files outside the current map in the evidence list. Do not insert or rearrange cards automatically.

### 4. Add selection focus

Why: displaying every connection at once can make the map harder to read.

- Implement file selection first. Highlight only direct incoming and outgoing links of enabled kinds.
- Apply fading to the visual presentation only. Do not hide or disable unrelated items.
- Show incoming and outgoing items separately in the evidence list.
- Allow selecting a symbol from that list to narrow the focus to its references.
- Treat clicking a symbol directly in a diff as a later step: deleted lines and partial diff text need reliable mapping to the head source first.
- Clear stale selections when switching reviews.

### 5. Keep analysis out of the UI thread

Why: workspace analysis must not freeze the review window or show links from a previous review.

- Run analysis automatically after reading changes, in a cancellable background process. Do not show an analysis button.
- Show checkout, installation, reading changes, finding links, and drawing as separate progress steps.
- Show live process details and file counts. Do not pretend the bar estimates remaining time.
- Draw file cards in short GTK batches so progress and cancellation remain responsive.
- Lock branch switching and chat until the build finishes or the cancelled worker stops.
- Show errors and incomplete coverage. Link-analysis failure may open the diff with a notice; checkout/install failure stops the build.
- Tie each result to the review clone and exact head commit. Discard results if the review changes.
- Cache by head commit, TypeScript version, dependency/configuration fingerprint, and analyser version.
- Detect local source changes before reusing results; do not assume the commit alone describes a dirty clone.
- Keep the map available without analysis results.

### 6. Add regression tests

Why: wrong links and misplaced arrows would mislead reviewers.

- Test relative imports, aliases, type-only imports, re-exports, project references, and repeated symbol uses.
- Test incoming uses from unchanged files and references outside the visible map.
- Test missing dependencies, unresolved targets, deleted files, cancellation, and stale results.
- Test both toggles, selection in both directions, fading, clearing selection, and unchanged card order.
- Check arrow placement and interaction during zoom and pan.
- Verify terminal and desktop launches on macOS and Linux. Report either platform as untested if it was not available.

## First-version boundary

Included: head-commit TypeScript import/reference analysis, file-card overlay, direct-link selection focus, evidence list, and symbol filtering through that list.

Not included: Nx integration, automatic card rearrangement, indirect dependency expansion, runtime execution tracing, base-versus-head link comparison, or direct symbol selection inside diff text.

## Remaining verification

Full-workspace coverage remains limited by the 150,000-source-location cap. That limit is unchanged. Each dependency worker also has a 110-second limit and analysis has an 8 GiB JavaScript heap limit, as requested by Julian. Check a real Linux desktop launch before claiming desktop coverage on both platforms.

Keep the existing unrelated chat and installer edits separate from this feature. They were not changed by this implementation.
