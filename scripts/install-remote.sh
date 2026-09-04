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

# --- cloudflared (Hermes public dashboard connector) --------------------
# Hermes stores a tunnel connector token in an owner-only file and requires
# cloudflared's --token-file support.  Ubuntu's bundled package can lag that
# capability, so use Cloudflare's signed package repository when it is absent
# or too old.  This installs the binary only; Hermes starts its own disabled-by-
# default user service when public exposure is explicitly confirmed.
log "checking cloudflared token-file support"
if command -v cloudflared >/dev/null 2>&1 \
    && cloudflared tunnel run --help 2>&1 | grep -q -- "--token-file"; then
    ok "cloudflared supports owner-only token files"
else
    log "installing current cloudflared from Cloudflare's signed repository"
    $SUDO apt-get update -qq
    $SUDO apt-get install -y ca-certificates curl gnupg
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
        | $SUDO tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
        | $SUDO tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
    $SUDO apt-get update -qq
    $SUDO apt-get install -y cloudflared
    cloudflared tunnel run --help 2>&1 | grep -q -- "--token-file"
    ok "cloudflared supports owner-only token files"
fi

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
if [[ "${SANDBOX_DEFER_RUNTIME_ACTIVATION:-0}" == "1" ]]; then
    ok "sandbox runtime activation deferred to the exact staged-source handoff"
elif [[ -x "$SANDBOX_HOME/sb-src/sb" ]]; then
    ok "sandbox runtime already present at $SANDBOX_HOME/sb-src"
elif [[ ! -d "$SANDBOX_HOME/sb-src/.git" ]]; then
    log "cloning the sandbox runtime into $SANDBOX_HOME/sb-src"
    git clone --depth 1 https://github.com/alimuzzaman/sandbox.git "$SANDBOX_HOME/sb-src"
else
    ok "sandbox runtime already present at $SANDBOX_HOME/sb-src"
fi

# --- CLI venv + visit tools venv (Playwright + headless Chromium) --------
# install.sh itself is interactive (asks y/N) and unsuitable for this
# non-interactive path, so call the same underlying functions it would:
# ensure_cli_venv (implicit on first `./sb` invocation) then ensure_tools_venv.
log "provisioning the CLI + visit tools venvs"
export SANDBOX_HOME
if [[ "${SANDBOX_DEFER_RUNTIME_ACTIVATION:-0}" != "1" ]]; then
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
fi
ok "provisioning complete"

# --- measured immutable image staging helper ----------------------------
# Provision only. This does not contact a registry, start a helper, or stage an
# image. Remote service migration calls the same owner-scoped installer after
# staging a new runtime, so service and helper revision parity cannot diverge.
log "provisioning measured image staging helper"
if [[ "${SANDBOX_DEFER_RUNTIME_ACTIVATION:-0}" == "1" ]]; then
    ok "image staging helper provisioning deferred to the exact staged-source handoff"
    exit 0
fi
STAGING_RUNTIME_REVISION="${SANDBOX_RUNTIME_REVISION:-}"
if [[ -z "$STAGING_RUNTIME_REVISION" && -d "$SANDBOX_HOME/sb-src/.git" ]]; then
    STAGING_RUNTIME_REVISION="$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
        -u GIT_PREFIX git -C "$SANDBOX_HOME/sb-src" rev-parse --verify HEAD 2>/dev/null || true)"
fi
python3 "$SANDBOX_HOME/sb-src/scripts/provision_image_stage_helper.py" \
    --sandbox-home "$SANDBOX_HOME" --runtime-revision "$STAGING_RUNTIME_REVISION"
ok "image staging helper provisioned"

# --- owned storage authority assets -------------------------------------
# Stage and optionally deploy systemd unit and tool assets for the owned storage
# authority. First-time remote provisioning verifies unit and script assets in
# the staged runtime without starting unapproved services or mutating privilege.
log "staging owned storage authority assets"
if [[ -d "$SANDBOX_HOME/sb-src/config/systemd" ]]; then
    for unit in sandbox-owned-storage.service sandbox-owned-storage.socket \
                sandbox-owned-storage-controller.service sandbox-owned-storage-controller.socket \
                sandbox-owned-storage-mount.service sandbox-owned-storage.sysusers; do
        if [[ ! -f "$SANDBOX_HOME/sb-src/config/systemd/$unit" ]]; then
            echo "missing required owned storage systemd unit template: $unit" >&2
            exit 1
        fi
    done
    ok "owned storage authority units verified in staged runtime"
fi

if [[ "${SANDBOX_DEPLOY_OWNED_STORAGE:-0}" == "1" ]]; then
    log "deploying owned storage systemd units and sysusers"
    if [[ -f "$SANDBOX_HOME/sb-src/config/systemd/sandbox-owned-storage.sysusers" ]]; then
        $SUDO cp "$SANDBOX_HOME/sb-src/config/systemd/sandbox-owned-storage.sysusers" /etc/sysusers.d/sandbox-owned-storage.conf 2>/dev/null || true
    fi
    for unit in sandbox-owned-storage.service sandbox-owned-storage.socket \
                sandbox-owned-storage-controller.service sandbox-owned-storage-controller.socket \
                sandbox-owned-storage-mount.service; do
        $SUDO cp "$SANDBOX_HOME/sb-src/config/systemd/$unit" "/etc/systemd/system/$unit" 2>/dev/null || true
    done
    if command -v systemctl >/dev/null 2>&1; then
        $SUDO systemctl daemon-reload 2>/dev/null || true
    fi
    ok "owned storage authority units deployed"
fi

