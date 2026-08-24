#!/usr/bin/env bash
# Rebuild the vendored Tailwind CSS for `./sb web` (config/sandbox-web.css).
#
# The web UI inlines a pre-built Tailwind stylesheet so it works offline with
# no CDN and no node_modules. Run this after changing Tailwind classes in the
# _WEB_PAGE markup inside sandbox/core/_paths.py. Uses the Tailwind *standalone* CLI (a single
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

# 2. Extract the page shell (_WEB_PAGE) from the canonical core paths module so
#    Tailwind can scan its classes. The old monolithic `sb` entrypoint no longer
#    owns this template; keeping the extraction here prevents dev/production
#    shell drift from breaking CSS builds.
python3 - "$ROOT" <<'PY'
import sys, os
root = sys.argv[1]
src = open(os.path.join(root, 'sandbox', 'core', '_paths.py')).read()
marker = '_WEB_PAGE = """'
start = src.index(marker) + len(marker)
end = src.index('"""', start)
open(os.path.join(root, '.cache', 'page.html'), 'w').write(src[start:end])
PY

# 3. Config mirrors xSpeed DESIGN.md §2 tokens + §5 radius.
cat > "$CACHE/tailwind.config.js" <<'EOF'
module.exports = {
  // Scan BOTH the inlined page shell (_WEB_PAGE, extracted to page.html) AND
  // the TypeScript dashboard source — after the TS migration most class names
  // live in src/web/src/*.ts template strings, not in _WEB_PAGE.
  content: [
    './.cache/page.html',
    './src/web/src/**/*.ts',
    './src/web/index.html',
  ],
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
