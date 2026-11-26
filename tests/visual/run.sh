#!/usr/bin/env bash
# Run both implementations against the same headless Linux desktop on macOS or Linux.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
mode=${1:-compare}
case "$mode" in compare|record-legacy|check-legacy) ;; *) echo "usage: $0 [compare|check-legacy|record-legacy]" >&2; exit 2 ;; esac
baseline=0d6518bb2a6ea4fd715c84f253a8c6529f987b81
reference=$(mktemp -d)
trap 'rm -rf "$reference"' EXIT
if [[ "$mode" != compare ]]; then git -C "$root" archive "$baseline" | tar -x -C "$reference"; fi
docker build -q -f "$root/tests/visual/Dockerfile" -t code-flow-visual "$root/tests/visual"
mkdir -p "$root/target/visual"
if [[ "$mode" == compare ]]; then
    docker run --rm -v "$root:/work:ro" -v code-flow-visual-build:/build \
        -v code-flow-visual-registry:/usr/local/cargo/registry \
        -e CARGO_TARGET_DIR=/build code-flow-visual cargo build --locked --bin code-flow
fi
goldens_mode=ro
if [[ "$mode" == record-legacy ]]; then goldens_mode=rw; fi
mkdir -p "$root/tests/visual/goldens"
docker run --rm --network none -v "$root:/work:ro" -v "$reference:/reference:ro" \
    -v "$root/target/visual:/results" -v "$root/tests/visual/goldens:/goldens:$goldens_mode" \
    -v code-flow-visual-build:/build:ro code-flow-visual \
    dbus-run-session -- xvfb-run -a -s '-screen 0 1600x1200x24 -dpi 96' \
    /usr/bin/python3 -u /work/tests/visual/scenarios.py "$mode"
