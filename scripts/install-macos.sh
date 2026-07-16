#!/usr/bin/env bash
# Bootstrap Sandbox on macOS from zero.
#
# Installs (with your confirmation): Homebrew, python3, Docker Desktop.
# OrbStack is also supported when it provides the active Docker context.
# Installs Reader.md by default when Homebrew is available, unless explicitly
# skipped. Then hands off to ./install.sh which runs `./sb setup`.
#
# Usage (from the sandbox clone root):
#   bash scripts/install-macos.sh
set -euo pipefail

cd "$(dirname "$0")/.."

B="$(printf '\033[1m')"; G="$(printf '\033[32m')"; Y="$(printf '\033[33m')"; N="$(printf '\033[0m')"
step() { printf '\n%s▸ %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
ask()  { printf '  Install %s now? [y/N] ' "$1"; read -r ans; [[ "$ans" =~ ^[yY] ]]; }

printf '\n%sWPDeveloper Sandbox — macOS bootstrap%s\n' "$B" "$N"

# --- Homebrew ---------------------------------------------------------------
step "1/4  Homebrew"
if command -v brew >/dev/null 2>&1; then
    ok "Homebrew already installed"
else
    warn "Homebrew not found (https://brew.sh)"
    if ask "Homebrew"; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # shellcheck disable=SC1091
        eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)" || true
        ok "Homebrew installed"
    else
        warn "Skipping — Homebrew is needed for the steps below."
    fi
fi

# --- python3 ----------------------------------------------------------------
step "2/4  Python 3"
if command -v python3 >/dev/null 2>&1; then
    VER="$(python3 --version 2>&1)"
    ok "python3 found ($VER)"
    # Check for venv module (needed for sandbox venvs)
    if ! python3 -m venv --help >/dev/null 2>&1; then
        warn "python3-venv module missing — reinstalling via Homebrew"
        brew install python
    fi
else
    warn "python3 not found"
    if ask "python3 via Homebrew"; then
        brew install python
        ok "python3 installed"
    else
        printf '  → install manually: https://www.python.org/downloads/\n'
    fi
fi

# --- Docker-compatible engine ----------------------------------------------
step "3/4  Docker-compatible engine"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker already running"
elif command -v docker >/dev/null 2>&1; then
    ENGINE_NAME="Docker Desktop"
    ENGINE_APP="Docker"
    if [[ "$(docker context show 2>/dev/null || true)" == "orbstack" ]]; then
        ENGINE_NAME="OrbStack"
        ENGINE_APP="OrbStack"
    fi
    warn "docker found but daemon is not running — open $ENGINE_NAME and try again."
    warn "Then re-run this script or continue with:  ./install.sh"
    open -a "$ENGINE_APP" 2>/dev/null || true
else
    warn "No Docker-compatible engine found. Install Docker Desktop or OrbStack."
    if ask "Docker Desktop via Homebrew"; then
        brew install --cask docker
        printf '\n  %s→ Open Docker Desktop once to accept the license, then%s\n' "$Y" "$N"
        printf '    re-run this script or continue with:  ./install.sh\n'
        open -a Docker 2>/dev/null || true
        exit 0
    else
        printf '  → install manually: https://www.docker.com/products/docker-desktop/\n'
    fi
fi

# --- Reader.md -------------------------------------------------------------
step "4/4  Reader.md"
if command -v reader >/dev/null 2>&1; then
    ok "Reader.md command-line tool already installed"
elif [[ "${SANDBOX_SKIP_READER_MD:-}" == "1" ]]; then
    warn "Skipping Reader.md (SANDBOX_SKIP_READER_MD=1)"
elif command -v brew >/dev/null 2>&1; then
    # Reader.md's cask lives in its own tap.  Its cask pins the release
    # archive checksum; Homebrew verifies it before installing the app.
    if brew tap jnahian/reader.md https://github.com/jnahian/reader.md \
        && brew trust --cask jnahian/reader.md/reader-md \
        && brew install --cask reader-md; then
        ok "Reader.md installed (open Sandbox docs with: reader .)"
    else
        warn "Reader.md could not be installed; continuing without it."
        warn "Retry later: brew tap jnahian/reader.md https://github.com/jnahian/reader.md && brew trust --cask jnahian/reader.md/reader-md && brew install --cask reader-md"
    fi
else
    warn "Reader.md needs Homebrew; skipping it."
    warn "Install later: brew tap jnahian/reader.md https://github.com/jnahian/reader.md && brew trust --cask jnahian/reader.md/reader-md && brew install --cask reader-md"
fi

# --- Hand off to install.sh -------------------------------------------------
printf '\n%s▸ Handing off to ./install.sh%s\n' "$B" "$N"
exec bash install.sh
