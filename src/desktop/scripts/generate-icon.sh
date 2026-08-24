#!/bin/sh
set -eu
case "$(uname -s)" in Darwin) ;; *) echo "macOS is required to generate icon.icns" >&2; exit 1;; esac
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
desktop_dir=$(dirname "$script_dir")
iconset="$desktop_dir/build/Sandbox.iconset"
rm -rf "$iconset"
mkdir -p "$iconset"
sips -s format png "$desktop_dir/assets/icon.svg" --out "$iconset/source.png" >/dev/null
for spec in "16 icon_16x16.png" "32 icon_16x16@2x.png" "32 icon_32x32.png" "64 icon_32x32@2x.png" "128 icon_128x128.png" "256 icon_128x128@2x.png" "256 icon_256x256.png" "512 icon_256x256@2x.png" "512 icon_512x512.png" "1024 icon_512x512@2x.png"; do
  set -- $spec
  sips -z "$1" "$1" "$iconset/source.png" --out "$iconset/$2" >/dev/null
done
rm "$iconset/source.png"
iconutil -c icns "$iconset" -o "$desktop_dir/build/icon.icns"
rm -rf "$iconset"
