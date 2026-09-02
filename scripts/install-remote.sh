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
if [[ -x "$SANDBOX_HOME/sb-src/sb" ]]; then
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

# --- measured immutable image staging helper ----------------------------
# Provision only. This does not contact a registry, start a helper, or stage an
# image. Each source digest gets its own non-replaced directory and manifest.
log "provisioning measured image staging helper"
STAGING_HELPER_SOURCE="$SANDBOX_HOME/sb-src/sandbox/hosting/images/staging_helper.py"
STAGING_HELPER_DIGEST="$(sha256sum "$STAGING_HELPER_SOURCE" | awk '{print $1}')"
STAGING_HELPER_ROOT="$SANDBOX_HOME/runtime/helpers/image-stage/sha256-$STAGING_HELPER_DIGEST"
python3 - "$SANDBOX_HOME" "$STAGING_HELPER_ROOT" <<'PY'
import os
from pathlib import Path
import stat
import sys

home = Path(sys.argv[1])
target = Path(sys.argv[2])
owner_uid = os.geteuid()
if not home.is_absolute() or target.parent.parent != home / "runtime" / "helpers":
    raise SystemExit("invalid staging helper directory identity")
fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
try:
    parts = target.parts[1:]
    home_parts = home.parts[1:]
    for index, part in enumerate(parts):
        owned = index >= len(home_parts)
        try:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            if not owned:
                raise SystemExit("staging helper parent directory is missing")
            os.mkdir(part, 0o700, dir_fd=fd)
            os.fsync(fd)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        info = os.fstat(child)
        # The pre-existing runtime directory may retain the user's legacy
        # 0775 mode.  The helper namespace itself is the isolation boundary:
        # every directory below `runtime/helpers` must be owner-only.
        protected = owned and index > len(home_parts)
        if owned and (info.st_uid != owner_uid or (protected and stat.S_IMODE(info.st_mode) & 0o077)):
            raise SystemExit("staging helper directory ownership or mode is unsafe")
        os.close(fd); fd = child
finally:
    os.close(fd)
os.chmod(target.parent, 0o700)
os.chmod(target, 0o700)
PY
if [[ -f "$STAGING_HELPER_ROOT/staging_helper.py" ]]; then
    STAGING_INSTALLED_DIGEST="$(sha256sum "$STAGING_HELPER_ROOT/staging_helper.py" | awk '{print $1}')"
    if [[ "$STAGING_INSTALLED_DIGEST" != "$STAGING_HELPER_DIGEST" ]]; then
        fail "installed image staging helper digest mismatch at $STAGING_HELPER_ROOT"
    fi
else
    install -m 0500 "$STAGING_HELPER_SOURCE" "$STAGING_HELPER_ROOT/staging_helper.py"
fi
STAGING_INSTALLED_DIGEST="$(sha256sum "$STAGING_HELPER_ROOT/staging_helper.py" | awk '{print $1}')"
if [[ "$STAGING_INSTALLED_DIGEST" != "$STAGING_HELPER_DIGEST" ]]; then
    fail "installed image staging helper could not be measured exactly"
fi
python3 - "$STAGING_HELPER_ROOT/staging_helper.py" <<'PY'
import os
import stat
import sys
owner_uid = os.geteuid()
info = os.lstat(sys.argv[1])
if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != owner_uid \
        or stat.S_IMODE(info.st_mode) != 0o500 or info.st_nlink != 1:
    raise SystemExit("installed staging helper artifact identity is unsafe")
PY
STAGING_RUNTIME_REVISION="$(git -C "$SANDBOX_HOME/sb-src" rev-parse HEAD)"
python3 - "$STAGING_HELPER_ROOT/manifest.json" "$STAGING_HELPER_DIGEST" "$STAGING_RUNTIME_REVISION" <<'PY'
import json
import os
from pathlib import Path
import sys
import tempfile

path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "artifact_digest": "sha256:" + sys.argv[2],
    "entry": "sandbox-image-stage-helper-v1",
    "runtime_revision": sys.argv[3],
    "capability_revision": "systemd-cgroup-v2-stage-v1",
}
encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
if path.exists():
    try:
        existing = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"could not read installed staging helper manifest: {exc}")
    if existing != encoded:
        raise SystemExit("installed staging helper manifest mismatch")
    raise SystemExit(0)
fd, temporary = tempfile.mkstemp(prefix=".manifest.", dir=path.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise SystemExit("concurrent staging helper manifest mismatch")
    parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
python3 - "$STAGING_HELPER_ROOT/manifest-v2.json" "$STAGING_HELPER_DIGEST" "$STAGING_RUNTIME_REVISION" <<'PY'
import json
import os
from pathlib import Path
import sys
import tempfile

path = Path(sys.argv[1])
payload = {
    "schema_version": 2,
    "artifact_digest": "sha256:" + sys.argv[2],
    "entry": "sandbox-image-stage-helper-v2",
    "runtime_revision": sys.argv[3],
    "capability_revision": "systemd-cgroup-v2-batch-stage-v2",
}
encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
if path.exists():
    try:
        existing = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"could not read installed v2 staging helper manifest: {exc}")
    if existing != encoded:
        raise SystemExit("installed v2 staging helper manifest mismatch")
    raise SystemExit(0)
fd, temporary = tempfile.mkstemp(prefix=".manifest-v2.", dir=path.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise SystemExit("concurrent v2 staging helper manifest mismatch")
    parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
python3 - "$STAGING_HELPER_ROOT" <<'PY'
import os
from pathlib import Path
import stat
import sys

root = Path(sys.argv[1])
owner_uid = os.geteuid()
for path, expected_mode, expected_type in (
    (root.parent, 0o700, "directory"), (root, 0o700, "directory"),
    (root / "staging_helper.py", 0o500, "file"),
    (root / "manifest.json", 0o600, "file"),
    (root / "manifest-v2.json", 0o600, "file"),
):
    info = os.lstat(path)
    valid_type = stat.S_ISDIR(info.st_mode) if expected_type == "directory" else stat.S_ISREG(info.st_mode)
    if not valid_type or stat.S_ISLNK(info.st_mode) or info.st_uid != owner_uid \
            or stat.S_IMODE(info.st_mode) != expected_mode \
            or (expected_type == "file" and info.st_nlink != 1):
        raise SystemExit(f"installed staging helper {expected_type} identity is unsafe")
PY
ok "image staging helper provisioned at sha256:$STAGING_HELPER_DIGEST revision $STAGING_RUNTIME_REVISION"
