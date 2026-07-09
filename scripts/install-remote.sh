#!/usr/bin/env bash
# Remote VPS bootstrap for sandbox's `./sb remote provision <name>` (spec 014).
#
# Runs NON-INTERACTIVELY over SSH (piped in via `bash -s` from
# sandbox/commands/remote.py's _cmd_provision) -- no confirmation prompts,
# unlike scripts/install-ubuntu.sh's interactive local flow.
#
# Installs: Tailscale (joins the tailnet if TAILSCALE_AUTHKEY is set in the
# environment; otherwise installs the package and leaves joining as a manual
# `tailscale up` step, since a non-interactive join genuinely needs a key),
# Docker CE + compose plugin, and the sandbox `sb` runtime itself.
#
# Tested on: Ubuntu 22.04 LTS, Ubuntu 24.04 LTS.
set -euo pipefail

log() { printf '  ▸ %s\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }

if [[ "$(id -u)" == "0" ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

# --- Tailscale ---------------------------------------------------------
log "installing Tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | $SUDO sh
fi
if tailscale status >/dev/null 2>&1; then
    ok "Tailscale already joined to a tailnet"
elif [[ -n "${TAILSCALE_AUTHKEY:-}" ]]; then
    $SUDO tailscale up --authkey="${TAILSCALE_AUTHKEY}" --ssh
    ok "Tailscale joined the tailnet"
else
    log "TAILSCALE_AUTHKEY not set -- installed but NOT joined; run "
    log "'sudo tailscale up' on this host manually, then re-run provision"
fi

# --- Docker CE + compose plugin -----------------------------------------
log "installing Docker"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker already running"
else
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
    USER="${USER:-$(id -un)}"
    if ! groups "$USER" | grep -q docker; then
        $SUDO usermod -aG docker "$USER"
    fi
    ok "Docker CE installed"
fi

# --- python3 + venv (needed for sb + the visit tools venv) ---------------
log "installing Python 3 + venv"
if ! python3 -m venv --help >/dev/null 2>&1; then
    $SUDO apt-get update -qq
    $SUDO apt-get install -y python3 python3-venv
fi
ok "python3 + venv present"

# --- sandbox runtime -----------------------------------------------------
SANDBOX_HOME="${SANDBOX_HOME:-$HOME/sandbox}"
mkdir -p "$SANDBOX_HOME"
if [[ ! -d "$SANDBOX_HOME/sb-src/.git" ]]; then
    log "cloning the sandbox runtime into $SANDBOX_HOME/sb-src"
    git clone --depth 1 https://github.com/templately/sandbox.git "$SANDBOX_HOME/sb-src"
else
    ok "sandbox runtime already present at $SANDBOX_HOME/sb-src"
fi

# --- CLI venv + visit tools venv (Playwright + headless Chromium) --------
# install.sh itself is interactive (asks y/N) and unsuitable for this
# non-interactive path, so call the same underlying functions it would:
# ensure_cli_venv (implicit on first `./sb` invocation) then ensure_tools_venv.
log "provisioning the CLI + visit tools venvs"
export SANDBOX_HOME
(
    cd "$SANDBOX_HOME/sb-src"
    ./sb --help >/dev/null 2>&1 || true   # bootstraps the CLI venv on first run
    "$SANDBOX_HOME/sb-src/.cli-venv/bin/python" -c \
        "from sandbox.core._config import ensure_tools_venv; ensure_tools_venv()"
)
ok "provisioning complete"
