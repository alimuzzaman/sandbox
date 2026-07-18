#!/usr/bin/env bash
# Remote VPS bootstrap for sandbox's `./sb remote provision <name>` (spec 014).
#
# Runs NON-INTERACTIVELY over SSH (piped in via `bash -s` from
# sandbox/commands/remote.py's _cmd_provision) -- no confirmation prompts,
# unlike scripts/install-ubuntu.sh's interactive local flow.
#
# Installs: Docker CE + compose plugin and the sandbox `sb` runtime itself.
# If SANDBOX_CONTROL_TRANSPORT=tailscale, also installs Tailscale and joins the
# tailnet when TAILSCALE_AUTHKEY is set. Public HTTPS is the default control
# plane and does not require Tailscale.
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

# --- Optional Tailscale ------------------------------------------------
SANDBOX_CONTROL_TRANSPORT="${SANDBOX_CONTROL_TRANSPORT:-https}"
if [[ "$SANDBOX_CONTROL_TRANSPORT" == "tailscale" ]]; then
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
else
    ok "Tailscale skipped (public HTTPS control plane)"
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
if command -v apt-get >/dev/null 2>&1; then
    PY_MM="$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
    $SUDO apt-get update -qq
    $SUDO apt-get install -y "python${PY_MM}-venv" python3-venv
fi
ok "python3 + venv present"

# --- act (remote CI execution engine) -----------------------------------
# Remote CI jobs run on this host's Docker daemon, so the provisioned runtime
# must include the same supported engine as the local CI command. Install the
# official architecture-specific release only when it is absent.
log "installing act"
if command -v act >/dev/null 2>&1; then
    ok "act already present"
else
    ACT_ARCH="$(dpkg --print-architecture)"
    case "$ACT_ARCH" in
        amd64) ACT_ARCH="x86_64" ;;
        arm64) ACT_ARCH="arm64" ;;
        *) echo "unsupported architecture for act: $ACT_ARCH" >&2; exit 1 ;;
    esac
    ACT_TMP="$(mktemp -d)"
    trap 'rm -rf "$ACT_TMP"' EXIT
    curl -fsSL "https://github.com/nektos/act/releases/latest/download/act_Linux_${ACT_ARCH}.tar.gz" \
        | tar -xz -C "$ACT_TMP"
    $SUDO install -m 0755 "$ACT_TMP/act" /usr/local/bin/act
    rm -rf "$ACT_TMP"
    trap - EXIT
    ok "act installed"
fi
act --version >/dev/null

# --- sandbox runtime -----------------------------------------------------
SANDBOX_HOME="${SANDBOX_HOME:-$HOME/sandbox}"
mkdir -p "$SANDBOX_HOME"
if [[ -x "$SANDBOX_HOME/sb-src/sb" ]]; then
    ok "sandbox runtime already present at $SANDBOX_HOME/sb-src"
elif [[ ! -d "$SANDBOX_HOME/sb-src/.git" ]]; then
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
    test -f sandbox/hermes/cron-catalog.json
    python3 - <<'PY'
import json
from pathlib import Path

root = Path("sandbox/hermes")
catalog = json.loads((root / "cron-catalog.json").read_text())
jobs = catalog.get("jobs")
if not isinstance(jobs, list):
    raise SystemExit("invalid Hermes cron catalog")
for job in jobs:
    script = job.get("script") if isinstance(job, dict) else None
    if script and (Path(script).name != script or not (root / "cron_scripts" / script).is_file()):
        raise SystemExit(f"missing committed Hermes cron script: {script}")
PY
    if [[ ! -x "$SANDBOX_HOME/sb-src/.cli-venv/bin/python" ]]; then
        python3 -m venv "$SANDBOX_HOME/sb-src/.cli-venv"
        "$SANDBOX_HOME/sb-src/.cli-venv/bin/pip" install --quiet \
            --disable-pip-version-check pyyaml
    fi
    ./sb mcp-install >/dev/null
    "$SANDBOX_HOME/sb-src/.cli-venv/bin/python" -c \
        "from sandbox.core._config import ensure_tools_venv; ensure_tools_venv()"
)
ok "provisioning complete"
