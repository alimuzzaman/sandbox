#!/usr/bin/env bash
# Bootstrap Sandbox on macOS from zero.
#
# Installs (with your confirmation): Homebrew, python3, Docker Desktop.
# Then hands off to ./install.sh which runs `./sb setup`.
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
step "1/3  Homebrew"
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
step "2/3  Python 3"
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

# --- Docker Desktop ---------------------------------------------------------
step "3/3  Docker Desktop"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker already running"
elif command -v docker >/dev/null 2>&1; then
    warn "docker found but daemon is not running — open Docker Desktop and try again."
    warn "Then re-run this script or continue with:  ./install.sh"
    open -a Docker 2>/dev/null || true
else
    warn "Docker Desktop not found (https://www.docker.com/products/docker-desktop/)"
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

# --- Hand off to install.sh -------------------------------------------------
printf '\n%s▸ Handing off to ./install.sh%s\n' "$B" "$N"
exec bash install.sh
