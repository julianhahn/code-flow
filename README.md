# Code Flow

A small Linux GUI for exploring code and opening source locations in Zed.

## Run

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
- Publish example: open the source-verified `publishDocumentRequest` flow.

## Reading the flow

- Amber IF: condition, with YES/NO paths.
- Blue CALL: function call.
- Red THROW: stop with an error.
- Green RETURN: finish the request.

The visual convention is right for sequence, down for a function drill-down, and dashed up for a return. Branch lanes do not imply a deeper function call.

## Current limits

The publication example in `flows/publish-request.json` is manually checked against its source, not automatically inferred from graph edges. Source hashes prevent showing it after its evidence changes. `publish-request-flow.py` is the old, unused prototype definition; retained only for comparison. It covers one function body, not the internals of every called function.

Search results open source files; arbitrary verified execution flows are not implemented. Tokensave call order alone does not prove runtime order. Free-text workflow search is not implemented.

This is a prototype, not production software. It contains a Plancraft-specific example and stays private.
