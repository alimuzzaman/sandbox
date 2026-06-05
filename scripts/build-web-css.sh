#!/usr/bin/env bash
# Rebuild the vendored Tailwind CSS for `./sb web` (config/sandbox-web.css).
#
# The web UI inlines a pre-built Tailwind stylesheet so it works offline with
# no CDN and no node_modules. Run this after changing Tailwind classes in the
# _WEB_PAGE markup inside `sb`. Uses the Tailwind *standalone* CLI (a single
# binary — no npm install), downloaded to .cache/ on first run.
#
# Usage:  ./scripts/build-web-css.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/.cache"
BIN="$CACHE/tailwindcss"
OUT="$ROOT/config/sandbox-web.css"
TW_VERSION="v3.4.17"

mkdir -p "$CACHE"

# 1. Fetch the standalone binary for this platform (once).
if [ ! -x "$BIN" ]; then
  uname_s="$(uname -s)"; uname_m="$(uname -m)"
  case "$uname_s-$uname_m" in
    Darwin-arm64)  asset="tailwindcss-macos-arm64" ;;
    Darwin-x86_64) asset="tailwindcss-macos-x64" ;;
    Linux-x86_64)  asset="tailwindcss-linux-x64" ;;
    Linux-aarch64) asset="tailwindcss-linux-arm64" ;;
    *) echo "unsupported platform $uname_s-$uname_m" >&2; exit 1 ;;
  esac
  echo "• downloading tailwindcss $TW_VERSION ($asset)…"
  curl -sL "https://github.com/tailwindlabs/tailwindcss/releases/download/$TW_VERSION/$asset" -o "$BIN"
  chmod +x "$BIN"
fi

# 2. Extract the page markup from sb so Tailwind can scan the classes it uses
#    (classes live in both static HTML and JS template strings — Tailwind's
#    content scanner reads the raw text, so it catches both).
python3 - "$ROOT" <<'PY'
import sys, types, os
root = sys.argv[1]
m = types.ModuleType('m'); m.__dict__['__file__'] = os.path.join(root, 'sb')
src = open(os.path.join(root, 'sb')).read()
src = src[src.index('from __future__'):].replace('\nif __name__', '\nif False and __name__')
exec(compile(src, 'sb', 'exec'), m.__dict__)
open(os.path.join(root, '.cache', 'page.html'), 'w').write(m._WEB_PAGE)
PY

# 3. Config mirrors xSpeed DESIGN.md §2 tokens + §5 radius.
cat > "$CACHE/tailwind.config.js" <<'EOF'
module.exports = {
  content: ['./.cache/page.html'],
  darkMode: 'media',
  // Safelist classes the regex scanner can miss inside minified JS template
  // literals (arbitrary values + state variants).
  safelist: [
    'w-44', 'w-64', 'backdrop-blur-sm',
  ],
  theme: { extend: {
    colors: {
      accent: '#2563eb',
      page:  { DEFAULT: '#f5f5f5', dark: '#050505' },
      app:   { DEFAULT: '#ffffff', dark: '#0a0a0a' },
      card:  { DEFAULT: '#ffffff', dark: '#171717' },
      brd:   { DEFAULT: '#e5e5e5', dark: '#2a2a2a' },
      brdin: { DEFAULT: '#d4d4d4', dark: '#3f3f46' },
      ink:   { DEFAULT: '#171717', dark: '#fafafa' },
    },
    fontFamily: { sans: ['-apple-system','BlinkMacSystemFont','Segoe UI','Roboto','sans-serif'] },
    borderRadius: { DEFAULT: '6px', lg: '10px' },
  } },
};
EOF
printf '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n' > "$CACHE/in.css"

# 4. Build minified, only the classes the page uses.
(cd "$ROOT" && "$BIN" -c "$CACHE/tailwind.config.js" -i "$CACHE/in.css" -o "$OUT" --minify)

echo "✓ wrote $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
