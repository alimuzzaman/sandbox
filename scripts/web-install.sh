#!/usr/bin/env sh
# Sandbox — one-line web installer (production).
#
#   curl -fsSL <BASE_URL>/install.sh | sh
#
# Host THIS file at <BASE_URL>/install.sh and the tarball (from
# scripts/make-release.sh) at <BASE_URL>/sandbox-latest.tar.gz. The dev repo can
# stay private — this never touches GitHub; it downloads the public tarball.
#
# What it does, step by step:
#   1. ensure curl/tar + python3 (offers to install python3 via brew/apt/dnf)
#   2. download + unpack the sandbox to ~/sandbox (override with SANDBOX_DIR)
#   3. run ./sb setup  (Docker check, WordPress, MCP — interactive, no sudo)
#
# Overridable via env:
#   SANDBOX_BASE_URL  where to fetch from   (default below)
#   SANDBOX_DIR       install location      (default: $HOME/sandbox)
set -eu

# ---- CONFIGURE ME: the public host serving install.sh + the tarball ----------
BASE_URL="${SANDBOX_BASE_URL:-https://sandbox.example.com}"
# -----------------------------------------------------------------------------
DIR="${SANDBOX_DIR:-$HOME/sandbox}"
TARBALL_URL="$BASE_URL/sandbox-latest.tar.gz"

if [ -t 1 ]; then
  B="$(printf '\033[1m')"; DIM="$(printf '\033[2m')"; G="$(printf '\033[32m')"
  Y="$(printf '\033[33m')"; R="$(printf '\033[31m')"; N="$(printf '\033[0m')"
else B=""; DIM=""; G=""; Y=""; R=""; N=""; fi
step() { printf '\n%s▸ %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n  %s✗ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

printf '\n%sWPDeveloper Sandbox — installer%s\n' "$B" "$N"
printf '%sA real local WordPress for Claude. Paste-and-go setup.%s\n' "$DIM" "$N"

# Refuse the obvious misconfig so users get a clear message, not a 404 blob.
case "$BASE_URL" in
  *example.com*) die "this installer hasn't been pointed at a host yet
       (BASE_URL is still the placeholder). Set SANDBOX_BASE_URL, e.g.:
       curl -fsSL <host>/install.sh | SANDBOX_BASE_URL=<host> sh" ;;
esac

# ---- pkg manager (for the python3 offer) ------------------------------------
PM=""; PM_CMD=""
if command -v brew >/dev/null 2>&1; then PM="Homebrew"; PM_CMD="brew install python"
elif command -v apt-get >/dev/null 2>&1; then PM="apt"; PM_CMD="sudo apt-get install -y python3 python3-venv"
elif command -v dnf >/dev/null 2>&1; then PM="dnf"; PM_CMD="sudo dnf install -y python3 python3-virtualenv"
fi

# ---- Step 1: base tools -----------------------------------------------------
step "Step 1/3  Checking base tools"
command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
  || die "need curl or wget to download the sandbox."
command -v tar >/dev/null 2>&1 || die "need tar to unpack the sandbox."
ok "curl/tar present"
if command -v python3 >/dev/null 2>&1; then
  ok "python3 found ($(python3 --version 2>&1))"
else
  warn "python3 not found — the sandbox CLI needs it."
  if [ -n "$PM_CMD" ] && [ -t 0 ]; then
    printf '  Install python3 via %s now? [y/N] ' "$PM"; read ans
    case "$ans" in
      y|Y|yes|YES) printf '  running: %s\n' "$PM_CMD"
        sh -c "$PM_CMD" && command -v python3 >/dev/null 2>&1 \
          && ok "python3 installed" \
          || die "python3 still missing — open a new shell and re-run." ;;
      *) die "python3 is required. Install it, then re-run:  $PM_CMD" ;;
    esac
  else
    die "python3 is required. Install it, then re-run:  ${PM_CMD:-see python.org}"
  fi
fi

# ---- Step 2: download + unpack ----------------------------------------------
step "Step 2/3  Downloading the sandbox"
if [ -e "$DIR" ] && [ -f "$DIR/sb" ]; then
  warn "$DIR already has a sandbox — leaving it in place (re-running setup)."
else
  [ -e "$DIR" ] && die "$DIR exists but isn't a sandbox. Move it or set SANDBOX_DIR."
  tmp="$(mktemp -d)"
  printf '  fetching %s\n' "$TARBALL_URL"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$TARBALL_URL" -o "$tmp/sandbox.tar.gz" \
      || die "download failed ($TARBALL_URL). Check the URL/host."
  else
    wget -qO "$tmp/sandbox.tar.gz" "$TARBALL_URL" \
      || die "download failed ($TARBALL_URL). Check the URL/host."
  fi
  mkdir -p "$DIR"
  # tarball has a top-level sandbox/ dir → strip it into $DIR.
  tar -xzf "$tmp/sandbox.tar.gz" -C "$DIR" --strip-components=1
  rm -rf "$tmp"
  ok "installed to $DIR"
fi

# ---- Step 3: run setup ------------------------------------------------------
step "Step 3/3  Running setup"
printf '%s  Checks Docker, boots WordPress, builds the MCP server, wires Claude.%s\n' "$DIM" "$N"
cd "$DIR"
./sb setup

printf '\n'
ok "Sandbox is ready in $DIR"
printf '\n  Next:\n'
printf '    cd %s\n' "$DIR"
printf '    ./sb web        # browser dashboard\n'
printf '    claude          # Claude can now drive your WordPress\n\n'
