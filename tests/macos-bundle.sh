#!/bin/bash
# Verify the installed Finder name and the icon images AppKit can load.
set -euo pipefail
[[ "$(uname -s)" == Darwin ]] || { echo 'Run this bundle test on macOS.' >&2; exit 1; }
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
app="$work/Code Flow.app"
"$root/install-macos.sh" "$app"

plist="$app/Contents/Info.plist"
for key in CFBundleName CFBundleDisplayName; do
    [[ "$(plutil -extract "$key" raw -o - "$plist")" == 'Code Flow' ]]
done
icon=$(plutil -extract CFBundleIconFile raw -o - "$plist")
iconutil -c iconset -o "$work/installed.iconset" "$app/Contents/Resources/$icon"
for points in 16 32 128 256 512; do
    for scale in 1 2; do
        pixels=$((points * scale))
        suffix=""
        if [[ "$scale" == 2 ]]; then suffix="@2x"; fi
        image="$work/installed.iconset/icon_${points}x${points}${suffix}.png"
        properties=$(sips -g pixelWidth -g pixelHeight -g hasAlpha "$image")
        for dimension in pixelWidth pixelHeight; do
            grep -Eq "^[[:space:]]*${dimension}: ${pixels}$" <<< "$properties"
        done
        grep -Eq '^[[:space:]]*hasAlpha: yes$' <<< "$properties"
    done
done

# Compare decoded pixels at Dock and Finder sizes with the shared Linux artwork.
for pixels in 64 1024; do
    points=$((pixels / 2))
    retina="$work/installed.iconset/icon_${points}x${points}@2x.png"
    sips -s format png -z "$pixels" "$pixels" "$root/code-flow.svg" --out "$work/expected.png" >/dev/null
    sips -s format bmp "$work/expected.png" --out "$work/expected.bmp" >/dev/null
    sips -s format bmp "$retina" --out "$work/installed.bmp" >/dev/null
    cmp "$work/expected.bmp" "$work/installed.bmp"
done
echo 'Verified the Code Flow bundle name and all ten standard and Retina icon representations.'
