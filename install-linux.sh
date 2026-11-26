#!/usr/bin/env bash
# Desktop entries and shell launches share the same tool and agent settings.
set -euo pipefail
[[ "$(uname -s)" == Linux ]] || { echo 'On macOS run ./install-macos.sh.' >&2; exit 1; }
root=$(cd "$(dirname "$0")" && pwd)
bin="${XDG_BIN_HOME:-$HOME/.local/bin}"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
lib="$data/code-flow/bin"
entry="$data/applications/code-flow.desktop"
for dest in "$bin/code-flow" "$lib/code-flow" "$entry"; do
    [[ ! -e "$dest" ]] || { echo "$dest already exists. Move it aside to install this build." >&2; exit 1; }
done
cargo build --locked --release --manifest-path "$root/Cargo.toml" --target-dir "$root/target"
mkdir -p "$bin" "$lib" "$data/applications" "$data/icons/hicolor/scalable/apps"
install -m 755 "$root/target/release/code-flow" "$lib/code-flow"
install -m 644 "$root/code-flow.svg" "$data/icons/hicolor/scalable/apps/code-flow.svg"
{
    echo '#!/usr/bin/env bash'
    printf 'export PATH=%q\n' "$PATH"
    printf 'export PI_CODING_AGENT_DIR=%q\n' "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
    for name in XDG_STATE_HOME CODE_FLOW_REVIEW_ROOT CODE_FLOW_PROTECTED_ROOT CODE_FLOW_REPO CODE_FLOW_ROOT; do
        if [[ -n "${!name:-}" ]]; then printf 'export %s=%q\n' "$name" "${!name}"; fi
    done
    printf 'exec %q "$@"\n' "$lib/code-flow"
} > "$bin/code-flow"
chmod 755 "$bin/code-flow"
cat > "$entry" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Code Flow
Comment=Review Git branches and pull requests
Exec="$bin/code-flow"
Icon=code-flow
StartupWMClass=code-flow
Terminal=false
Categories=Development;
DESKTOP
printf 'Installed %s and %s\n' "$bin/code-flow" "$entry"
