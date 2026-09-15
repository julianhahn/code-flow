# Agent-authored flows

## Goal

Agents describe the relevant execution flow. The program checks source references and places the cards. Agents never supply coordinates or executable Python.

Code tools are optional evidence providers. A parser, tokensave, source inspection, or another tool may supply facts. No tool must discover the whole system.

## Workflow

1. Use tokensave to locate entry points and related functions.
2. Read the source. Establish execution order, conditions, awaits and early exits. An indexed call list does not prove execution order.
3. Write a version 1 JSON file. See `flows/publish-request.json`.
4. Validate it:

```sh
python3 flow_model.py flows/publish-request.json --root /home/julian/plancraft
```

5. Open it using **Open flow JSON** in the GUI.

## Fields

- `version`: currently `1`.
- `title`: the displayed flow title.
- `regions`: named function/runtime boundaries. Each has `id`, `label`, and optional `parent`. A called function's region points to its caller's region. Separate runtimes have separate root regions.
- `nodes`: `id`, `title`, `kind`, `region`, `source`, optional `hint`.
- `kind`: `call`, `condition`, `throw`, `return`, or `step`.
- `source`: checkout-relative `file`, one-based `line`, and whole-file `sha256`.
- `edgeTypes`: extensible names mapped to a supported layout `relation`.
- `edges`: `from`, `to`, `type`, `origin`, `evidence`, optional `label`.

Evidence uses the same shape as `source`. Paths cannot escape the configured checkout. A changed source fingerprint rejects the flow until an agent reviews and updates it.

### Connections

Supported layout relations:

- `sequence`: continuation in the same function region.
- `call`: enter a direct child function region.
- `return`: rise to the direct caller.
- `handoff`: move to another runtime entry, such as HTTP or a queued task.
- `response`: return across a runtime boundary.
- `loop`: explicitly marked back edge. It is not part of forward layout ranking.

Custom names do not require changing Python. For example:

```json
{
  "edgeTypes": {
    "firebase-task": {"relation": "handoff"},
    "firestore-trigger": {"relation": "handoff"},
    "http": {"relation": "handoff"}
  }
}
```

A queue link is agent-authored when the agent verifies the queue producer and handler registration. Include source evidence for both ends. Do not infer the link from similar names.

Origins:

- `tool`: extracted by a code tool.
- `agent`: verified by an agent reading source.
- `uncertain`: not established. The GUI shows a warning and marks the edge UNVERIFIED. It is not a verified sequence.

Origins record provenance, not a correctness guarantee. Source fingerprints show that evidence has not changed; they do not prove the edge's meaning.

## Determinism

Stable node IDs drive topological ordering and branch placement. Reordering JSON arrays does not change node positions. Layout derives sequence columns, branch lanes and function/runtime bands. No agent-selected coordinates.

## Current limits

There is no TypeScript parser or automatic cross-runtime discovery yet. Agents still author execution semantics. Return/throw handling inside called functions remains unknown until those functions are inspected.

The renderer is a prototype. Dense edge labels can overlap; call/return routing and large-flow navigation need more work. Loops have explicit labels, not a full loop layout. Parallel behavior needs explicit nodes and explanations; there is no concurrency simulator.

The default example remains the verified publish API body. This change does not claim to establish the entire UI-to-worker path.
