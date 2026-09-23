#!/bin/bash
# Create a Finder launcher. Dependencies must already be installed with Homebrew.
set -euo pipefail
[[ "$(uname -s)" == Darwin ]] || { echo 'This installer requires macOS.' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")" && pwd)"
BREW="$(command -v brew)"
PREFIX="$("$BREW" --prefix)"
PYTHON="$PREFIX/bin/python3"
"$PYTHON" -c 'import gi, cairo; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk'
APP="$HOME/Applications/Code Flow.app"
if [[ -e "$APP" ]]; then
  echo "$APP already exists. Move it aside before reinstalling." >&2
  exit 1
fi
mkdir -p "$APP/Contents/MacOS"
"$PYTHON" - "$APP" "$ROOT" "$PYTHON" "$PREFIX" "$PATH" <<'PY'
import pathlib, plistlib, shlex, sys
app, root, python, prefix, path = sys.argv[1:]
contents = pathlib.Path(app) / 'Contents'
with (contents / 'Info.plist').open('wb') as stream:
    plistlib.dump(dict(CFBundleName='Code Flow', CFBundleDisplayName='Code Flow',
                      CFBundleIdentifier='dev.julianhahn.code-flow', CFBundleVersion='1',
                      CFBundlePackageType='APPL', CFBundleExecutable='code-flow',
                      NSHighResolutionCapable=True), stream)
launcher = contents / 'MacOS/code-flow'
launcher.write_text('#!/bin/bash\n'
                    'export PATH=' + shlex.quote(prefix + '/bin:' + path) + '\n'
                    'exec ' + shlex.quote(python) + ' ' + shlex.quote(root + '/review_app.py') + ' "$@"\n')
launcher.chmod(0o755)
PY
printf 'Installed: %s\nStart with: open "%s"\n' "$APP" "$APP"
