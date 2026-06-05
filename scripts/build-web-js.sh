#!/usr/bin/env bash
# Rebuild the vendored dashboard bundle for `./sb web` (config/sandbox-web.js).
#
# The web UI is authored in TypeScript under src/web and built with Vite to a
# single self-contained IIFE file, which `sb` inlines into the served page. So
# running `./sb web` needs no node/node_modules — only REBUILDING does.
#
# Run this after editing anything under src/web, then restart `./sb web`.
# (Sibling of scripts/build-web-css.sh, which rebuilds the Tailwind CSS.)
#
# Usage:  ./scripts/build-web-js.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="$ROOT/src/web"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found — install Node.js to rebuild the web bundle." >&2
  exit 1
fi

# Install deps on first run (or when lockfile changed). Prefer `npm ci` when a
# lockfile exists; fall back to `npm install`.
if [ ! -d "$WEB/node_modules" ]; then
  echo "• installing src/web dev deps…"
  if [ -f "$WEB/package-lock.json" ]; then
    npm --prefix "$WEB" ci
  else
    npm --prefix "$WEB" install
  fi
fi

echo "• typechecking…"
npm --prefix "$WEB" run typecheck

echo "• building bundle → config/sandbox-web.js…"
npm --prefix "$WEB" run build

echo "✓ wrote $ROOT/config/sandbox-web.js"
echo "  restart ./sb web to pick it up."
