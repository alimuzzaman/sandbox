#!/usr/bin/env bash
# Bootstrap Sandbox on Ubuntu / Debian from zero.
#
# Installs (with your confirmation): python3 + venv, Docker CE.
# Then hands off to ./install.sh which runs `./sb setup`.
#
# Usage (from the sandbox clone root):
#   bash scripts/install-ubuntu.sh
#
# Tested on: Ubuntu 22.04 LTS, Ubuntu 24.04 LTS, Debian 12.
set -euo pipefail

cd "$(dirname "$0")/.."

B="$(printf '\033[1m')"; G="$(printf '\033[32m')"; Y="$(printf '\033[33m')"; N="$(printf '\033[0m')"
step() { printf '\n%s▸ %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
ask()  { printf '  Install %s now? [y/N] ' "$1"; read -r ans; [[ "$ans" =~ ^[yY] ]]; }

printf '\n%sWPDeveloper Sandbox — Ubuntu/Debian bootstrap%s\n' "$B" "$N"

if [[ "$(id -u)" == "0" ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

# --- python3 + venv ---------------------------------------------------------
step "1/2  Python 3 + venv"
need_python=0
if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 not found"
    need_python=1
elif ! python3 -m venv --help >/dev/null 2>&1; then
    warn "python3 found but python3-venv module is missing (Debian/Ubuntu split package)"
    need_python=1
else
    ok "python3 + venv found ($(python3 --version 2>&1))"
fi

if [[ "$need_python" == "1" ]]; then
    if ask "python3 + python3-venv via apt"; then
        $SUDO apt-get update -qq
        $SUDO apt-get install -y python3 python3-venv
        ok "python3 + python3-venv installed"
    else
        warn "Skipping — python3 is required."
    fi
fi

# --- Docker CE --------------------------------------------------------------
step "2/2  Docker"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker already running"
elif command -v docker >/dev/null 2>&1; then
    warn "docker found but not running"
    if $SUDO systemctl start docker 2>/dev/null; then
        sleep 2
        if docker info >/dev/null 2>&1 || $SUDO docker info >/dev/null 2>&1; then
            ok "Docker daemon started"
        else
            # User may need to be in the docker group.
            USER="${USER:-$(id -un)}"
            warn "Docker is running but this user can't access it."
            printf '  → Add yourself to the docker group, then re-login:\n'
            printf '      sudo usermod -aG docker %s\n' "$USER"
            printf '      newgrp docker   # or log out/in\n'
            printf '    Then re-run this script or: ./install.sh\n'
            exit 1
        fi
    else
        warn "Could not start Docker — check systemd logs: journalctl -u docker"
    fi
else
    warn "Docker not found (https://docs.docker.com/engine/install/ubuntu/)"
    if ask "Docker CE via apt (official Docker repo)"; then
        $SUDO apt-get update -qq
        $SUDO apt-get install -y ca-certificates curl gnupg lsb-release
        $SUDO install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
            | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
        ARCH="$(dpkg --print-architecture)"
        CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
        echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
            | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
        $SUDO apt-get update -qq
        $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io \
            docker-buildx-plugin docker-compose-plugin
        $SUDO systemctl enable --now docker
        # Add current user to the docker group so they don't need sudo.
        USER="${USER:-$(id -un)}"
        if ! groups "$USER" | grep -q docker; then
            $SUDO usermod -aG docker "$USER"
            warn "Added $USER to the docker group."
            printf '  → You must %slog out and back in%s for this to take effect,\n' "$Y" "$N"
            printf '    then re-run this script or continue with:  ./install.sh\n'
            exit 0
        fi
        ok "Docker CE installed and running"
    else
        printf '  → install manually: https://docs.docker.com/engine/install/ubuntu/\n'
    fi
fi

# --- Hand off to install.sh -------------------------------------------------
printf '\n%s▸ Handing off to ./install.sh%s\n' "$B" "$N"
exec bash install.sh
