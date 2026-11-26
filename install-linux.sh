#!/usr/bin/env bash
# Desktop entries and terminal launches use the same tool and Pi settings.
set -euo pipefail
[[ "$(uname -s)" == Linux ]] || { echo 'On macOS run ./install-macos.sh.' >&2; exit 1; }
root=$(cd "$(dirname "$0")" && pwd)
bin="${XDG_BIN_HOME:-$HOME/.local/bin}"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
lib="$data/code-flow/bin"
entry="$data/applications/code-flow.desktop"
icon="$data/icons/hicolor/scalable/apps/code-flow.svg"
destinations=("$lib/code-flow" "$icon" "$bin/code-flow" "$entry")
for dest in "${destinations[@]}"; do
    if [[ -e "$dest" || -L "$dest" ]]; then
        echo "$dest already exists. Move it aside to install this build." >&2
        exit 1
    fi
done
cargo build --locked --release --manifest-path "$root/Cargo.toml" --target-dir "$root/target"

# A hard link installs a complete file atomically and refuses a concurrent install.
# Stage beside each destination so XDG locations may be on separate filesystems.
pending=()
created=()
cleanup() {
    local status=$?
    if ((status != 0)); then
        for dest in "${created[@]}"; do rm -f -- "$dest"; done
    fi
    for tmp in "${pending[@]}"; do rm -f -- "$tmp"; done
}
trap cleanup EXIT
for dest in "${destinations[@]}"; do
    parent=$(dirname "$dest")
    mkdir -p "$parent"
    pending+=("$(mktemp "$parent/.code-flow.XXXXXX")")
done
install -m 755 "$root/target/release/code-flow" "${pending[0]}"
install -m 644 "$root/code-flow.svg" "${pending[1]}"
{
    echo '#!/usr/bin/env bash'
    printf 'export PATH=%q\n' "$PATH"
    printf 'export PI_CODING_AGENT_DIR=%q\n' "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
    for name in XDG_STATE_HOME CODE_FLOW_REVIEW_ROOT CODE_FLOW_PROTECTED_ROOT CODE_FLOW_REPO CODE_FLOW_ROOT; do
        if [[ -n "${!name:-}" ]]; then printf 'export %s=%q\n' "$name" "${!name}"; fi
    done
    printf 'exec %q "$@"\n' "$lib/code-flow"
} > "${pending[2]}"
chmod 755 "${pending[2]}"
cat > "${pending[3]}" <<DESKTOP
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
chmod 644 "${pending[3]}"
for i in "${!destinations[@]}"; do
    ln -T -- "${pending[i]}" "${destinations[i]}"
    created+=("${destinations[i]}")
done
printf 'Installed %s and %s\n' "$bin/code-flow" "$entry"
