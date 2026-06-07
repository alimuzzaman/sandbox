#!/usr/bin/env bash
# Stage the install site's public files:
#   public/install.sh             (web-install.sh with BASE_URL baked in)
#   public/sandbox-latest.tar.gz  (fresh release tarball)
#
# Set SANDBOX_BASE_URL to the public URL this site will serve at.
#
# Usage:
#   SANDBOX_BASE_URL=https://sandbox.xc1.app ./publish.sh
#   docker compose up -d
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"          # repo root
PUBLIC="$HERE/public"
BASE_URL="${SANDBOX_BASE_URL:-https://sandbox.xc1.app}"

# Release version from sandbox.yml (`version: N`), fallback to short git sha —
# matches scripts/make-release.sh so the badge on the page == the tarball name.
VER="$(awk -F': *' '/^version:/{print $2; exit}' "$ROOT/sandbox.yml" 2>/dev/null || true)"
[ -n "${VER:-}" ] || VER="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)"

mkdir -p "$PUBLIC"

echo "• building release tarball…"
"$ROOT/scripts/make-release.sh" >/dev/null
cp "$ROOT/dist/sandbox-latest.tar.gz" "$PUBLIC/sandbox-latest.tar.gz"
# also publish the pinned version, if present
ver_tar="$(ls -1 "$ROOT"/dist/sandbox-*.tar.gz 2>/dev/null | grep -v latest | head -1 || true)"
[ -n "$ver_tar" ] && cp "$ver_tar" "$PUBLIC/"

echo "• staging install.sh with BASE_URL=${BASE_URL} …"
# Bake the chosen BASE_URL into the served installer so end users don't have to
# set SANDBOX_BASE_URL themselves. We replace just the placeholder host on the
# BASE_URL line (keeping the ${SANDBOX_BASE_URL:-...} override intact), using a
# Python rewrite to avoid shell/sed quoting pitfalls with ${...} and URLs.
python3 - "$ROOT/scripts/web-install.sh" "$PUBLIC/install.sh" "$BASE_URL" <<'PY'
import re, sys
src, dst, base = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(src).read()
# Replace the default inside: BASE_URL="${SANDBOX_BASE_URL:-<default>}"
text = re.sub(
    r'(BASE_URL="\$\{SANDBOX_BASE_URL:-)[^}]*(\}")',
    lambda m: m.group(1) + base + m.group(2),
    text, count=1)
open(dst, "w").write(text)
PY
chmod +x "$PUBLIC/install.sh"

echo "• staging index.html with version ${VER} …"
# Bake the release version into the served landing page (replaces {{VERSION}}).
python3 - "$ROOT/deploy/install-image/index.html" "$PUBLIC/index.html" "$VER" <<'PY'
import sys
src, dst, ver = sys.argv[1], sys.argv[2], sys.argv[3]
open(dst, "w").write(open(src).read().replace("{{VERSION}}", ver))
PY

echo "✓ staged:"
ls -lh "$PUBLIC"
echo
echo "  Next:  docker compose up -d   (serves on :8088 → put behind your edge)"
echo "  Test:  curl -fsSL $BASE_URL/install.sh | sh"
