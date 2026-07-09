#!/usr/bin/env bash
# Bootstrap Sandbox on Arch Linux (or an Arch derivative: Manjaro, EndeavourOS)
# from zero.
#
# Installs (with your confirmation): python (Arch's `python` package already
# includes venv — no separate split package like Debian/Ubuntu), Docker +
# docker-compose (both official `extra` repo packages).
# Then hands off to ./install.sh which runs `./sb setup`.
#
# Usage (from the sandbox clone root):
#   bash scripts/install-arch.sh
set -euo pipefail

cd "$(dirname "$0")/.."

B="$(printf '\033[1m')"; G="$(printf '\033[32m')"; Y="$(printf '\033[33m')"; N="$(printf '\033[0m')"
step() { printf '\n%s▸ %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
ask()  { printf '  Install %s now? [y/N] ' "$1"; read -r ans; [[ "$ans" =~ ^[yY] ]]; }

printf '\n%sWPDeveloper Sandbox — Arch Linux bootstrap%s\n' "$B" "$N"

if [[ "$(id -u)" == "0" ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

# --- python3 + venv ----------------------------------------------------------
# Arch's `python` package bundles venv (unlike Debian/Ubuntu's split
# python3-venv package) — a missing python3-venv module here almost always
# means python3 itself is missing, not a split package to install separately.
step "1/2  Python 3"
if command -v python3 >/dev/null 2>&1 && python3 -m venv --help >/dev/null 2>&1; then
    ok "python3 + venv found ($(python3 --version 2>&1))"
else
    warn "python3 (with venv) not found"
    if ask "python via pacman"; then
        $SUDO pacman -Sy --noconfirm python
        ok "python installed"
    else
        warn "Skipping — python3 is required."
    fi
fi

# --- Docker + docker-compose --------------------------------------------------
# Both are official Arch `extra` repo packages (verified: pacman -Si docker,
# pacman -Si docker-compose) — no third-party repo needed, unlike Ubuntu's
# apt.docker.com setup.
step "2/2  Docker"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker already running"
elif command -v docker >/dev/null 2>&1; then
    warn "docker found but not running"
    if $SUDO systemctl start docker.service 2>/dev/null; then
        sleep 2
        if docker info >/dev/null 2>&1 || $SUDO docker info >/dev/null 2>&1; then
            ok "Docker daemon started"
        else
            USER="${USER:-$(id -un)}"
            warn "Docker is running but this user can't access it."
            printf '  → Add yourself to the docker group, then re-login:\n'
            printf '      sudo usermod -aG docker %s\n' "$USER"
            printf '      newgrp docker   # or log out/in\n'
            printf '    Then re-run this script or: ./install.sh\n'
            exit 1
        fi
    else
        warn "Could not start Docker — check: sudo journalctl -u docker"
    fi
else
    warn "Docker not found"
    if ask "docker + docker-compose via pacman"; then
        $SUDO pacman -Sy --noconfirm docker docker-compose
        $SUDO systemctl enable --now docker.service
        USER="${USER:-$(id -un)}"
        if ! groups "$USER" | grep -q docker; then
            $SUDO usermod -aG docker "$USER"
            warn "Added $USER to the docker group."
            printf '  → You must %slog out and back in%s for this to take effect,\n' "$Y" "$N"
            printf '    then re-run this script or continue with:  ./install.sh\n'
            exit 0
        fi
        ok "Docker installed and running"
    else
        printf '  → install manually: https://wiki.archlinux.org/title/Docker\n'
    fi
fi

# --- Hand off to install.sh --------------------------------------------------
printf '\n%s▸ Handing off to ./install.sh%s\n' "$B" "$N"
exec bash install.sh
