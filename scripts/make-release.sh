#!/usr/bin/env bash
# Build a runtime-only release tarball for the public `curl | sh` installer.
#
# Ships ONLY what running the sandbox needs (sb, config/, mcp/, tools/, skills/,
# workflows/, sandbox.yml, docker-compose.yml, README/CLAUDE). Excludes dev-only
# and secret/generated files: .git, src/web (TypeScript source — the built
# bundle in config/ ships instead), node_modules, .cli-venv, runtime/,
# *.local.yml, .env*, per-instance state, caches. Also pruned below as
# maintainer-only: deploy/ (release tooling), docs/ (internal planning),
# skills/sandbox-release (this release flow), the build-* + make-release scripts.
#
# Output:  dist/sandbox-<version>.tar.gz  +  dist/sandbox-latest.tar.gz
# Upload both to your public host (the install.sh BASE_URL).
#
# Usage:  ./scripts/make-release.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Version from sandbox.yml (`version: N`), fallback to short git sha.
VER="$(awk -F': *' '/^version:/{print $2; exit}' sandbox.yml 2>/dev/null || true)"
[ -n "${VER:-}" ] || VER="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"

OUT_DIR="$ROOT/dist"
mkdir -p "$OUT_DIR"
TARBALL="$OUT_DIR/sandbox-${VER}.tar.gz"
LATEST="$OUT_DIR/sandbox-latest.tar.gz"

echo "• building runtime tarball for version ${VER}…"

# Make sure the vendored web assets are current (they ship in config/).
if [ -f "$ROOT/scripts/build-web-css.sh" ]; then
  echo "• refreshing vendored web CSS…"; "$ROOT/scripts/build-web-css.sh" >/dev/null 2>&1 || \
    echo "  (skipped CSS rebuild — using existing config/sandbox-web.css)"
fi
if [ -f "$ROOT/scripts/build-web-js.sh" ] && [ -d "$ROOT/src/web/node_modules" ]; then
  echo "• refreshing vendored web JS…"; "$ROOT/scripts/build-web-js.sh" >/dev/null 2>&1 || \
    echo "  (skipped JS rebuild — using existing config/sandbox-web.js)"
fi

# git archive ships only COMMITTED files (so nothing local/secret leaks), into a
# sandbox/ top-level dir. Then we strip dev-only paths from the staged copy.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git archive --format=tar --prefix=sandbox/ HEAD | tar -x -C "$STAGE"

# Prune things the runtime doesn't need (belt-and-suspenders; git archive
# already excludes gitignored files like runtime/, .cli-venv, *.local.yml).
rm -rf \
  "$STAGE/sandbox/src" \
  "$STAGE/sandbox/runtime" \
  "$STAGE/sandbox/scripts/make-release.sh" \
  "$STAGE/sandbox/scripts/build-web-js.sh" \
  "$STAGE/sandbox/scripts/build-web-css.sh" \
  "$STAGE/sandbox/skills/sandbox-release" \
  "$STAGE/sandbox/deploy" \
  "$STAGE/sandbox/docs" \
  "$STAGE/sandbox/.github" 2>/dev/null || true

# Sanity: the built web bundle MUST be present (it's what `./sb web` serves).
if [ ! -f "$STAGE/sandbox/config/sandbox-web.js" ]; then
  echo "✗ config/sandbox-web.js missing from the archive — commit the built" >&2
  echo "  bundle (scripts/build-web-js.sh) before releasing." >&2
  exit 1
fi

# COPYFILE_DISABLE stops macOS bsdtar from writing ._* / xattr headers that
# GNU tar on Linux warns about ("Ignoring unknown extended header keyword").
COPYFILE_DISABLE=1 tar -czf "$TARBALL" -C "$STAGE" sandbox
cp "$TARBALL" "$LATEST"

SIZE="$(du -h "$TARBALL" | cut -f1)"
echo "✓ wrote $TARBALL ($SIZE)"
echo "✓ wrote $LATEST"
echo
echo "  Upload BOTH to your public host, e.g.:"
echo "    <BASE_URL>/sandbox-latest.tar.gz   (what install.sh downloads)"
echo "    <BASE_URL>/sandbox-${VER}.tar.gz    (pinned version)"
echo "  and host scripts/web-install.sh at <BASE_URL>/install.sh"
