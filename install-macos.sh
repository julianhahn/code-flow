#!/bin/bash
# Build and install a native Finder launcher. Existing applications are preserved.
set -euo pipefail
[[ "$(uname -s)" == Darwin ]] || { echo 'On Linux run ./install-linux.sh.' >&2; exit 1; }
root="$(cd "$(dirname "$0")" && pwd)"
app="${1:-$HOME/Applications/Code Flow.app}"
[[ "$app" = *.app ]] || { echo 'Specify an application path ending in .app.' >&2; exit 2; }
[[ ! -e "$app" ]] || { echo "$app already exists. Choose another destination." >&2; exit 1; }
cargo build --locked --release --manifest-path "$root/Cargo.toml" --target-dir "$root/target"
mkdir -p "$(dirname "$app")"
stage=$(mktemp -d "$(dirname "$app")/.code-flow.XXXXXX")
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/Contents/MacOS"
cat > "$stage/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Code Flow</string>
<key>CFBundleDisplayName</key><string>Code Flow</string>
<key>CFBundleIdentifier</key><string>dev.julianhahn.code-flow.rust</string>
<key>CFBundleVersion</key><string>2</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleExecutable</key><string>code-flow</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cp "$root/target/release/code-flow" "$stage/Contents/MacOS/code-flow-native"
{
    echo '#!/bin/bash'
    printf 'export PATH=%q\n' "$PATH"
    printf 'export PI_CODING_AGENT_DIR=%q\n' "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
    for name in XDG_STATE_HOME CODE_FLOW_REVIEW_ROOT CODE_FLOW_PROTECTED_ROOT CODE_FLOW_REPO CODE_FLOW_ROOT; do
        if [[ -n "${!name:-}" ]]; then printf 'export %s=%q\n' "$name" "${!name}"; fi
    done
    echo 'exec "$(dirname "$0")/code-flow-native" "$@"'
} > "$stage/Contents/MacOS/code-flow"
chmod +x "$stage/Contents/MacOS/code-flow"
plutil -lint "$stage/Contents/Info.plist"
mv "$stage" "$app"
printf 'Installed %s\n' "$app"
