"""Strict-mode runtime pins (spec 061 FR-007..FR-015).

A pin is a small JSON file under ``<remote SANDBOX_HOME>/runtime/remote-pins``
naming one controller checkout that requires the exact installed revision.
One fixed program, run over the existing authenticated SSH transport under a
remote flock, owns every read and write; this module never parses remote
output it has not validated. Pins carry no secrets and expire (at most four
hours after their last renewal); expiry is the only stale-holder detection.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import time
from dataclasses import dataclass
from typing import Callable

SCHEMA = 1
DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 4 * 3600
MAX_LISTED = 64  # also the remote capacity: registration past it is refused
INSTALL_LOCK_WAIT_SECONDS = 20
MAX_PURPOSE = 120
MAX_CHECKOUT = 4096
TIMEOUT_SECONDS = 15

HOLDER_RE = re.compile(r"h-[0-9a-f]{16}")
REVISION_RE = re.compile(r"[0-9a-f]{24}")
DIGEST_RE = re.compile(r"[0-9a-f]{16}")
ACTIVE = "active"
BROKEN = "broken"

PIN_UNVERIFIABLE = "strict_pin_unverifiable"
PINS_UNAVAILABLE = "pins_unavailable"
REPLACEMENT_IN_PROGRESS = "runtime_replacement_in_progress"
PINS_UNACKNOWLEDGED = "remote_runtime_pins_unacknowledged"
PIN_CAPACITY_REACHED = "pin_capacity_reached"


class PinError(RuntimeError):
    """A pin operation failed; ``code`` is a fixed vocabulary value."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _digest16(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def holder_identity(home: str | os.PathLike, checkout: str | os.PathLike) -> str:
    """``h-`` + 16 hex of sha256(local home realpath NUL checkout realpath)."""
    return "h-" + _digest16(f"{os.path.realpath(home)}\0{os.path.realpath(checkout)}")


def _printable(value: str) -> bool:
    return all(ch.isprintable() for ch in value)


def _secret_free(value: str) -> bool:
    from sandbox.services.redaction import redact_text
    return redact_text(value) == value


def purpose_for(command: str, checkout: str) -> str:
    """A bounded, printable purpose from the command and checkout path."""
    text = " ".join(f"{command} {checkout}".split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:MAX_PURPOSE]


@dataclass(frozen=True)
class Pin:
    holder: str
    checkout_path: str
    controller_home_digest: str
    revision: str
    purpose: str
    registered_at: int
    renewed_at: int
    expires_at: int
    state: str = ACTIVE
    broken_by: str | None = None
    broken_at: int | None = None

    def expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) >= self.expires_at

    def binding(self, now: float | None = None) -> bool:
        """An unexpired, unbroken pin binds the installed revision."""
        return self.state == ACTIVE and not self.expired(now)

    def as_mapping(self, now: float | None = None) -> dict:
        return {
            "schema": SCHEMA, "holder": self.holder,
            "checkout_path": self.checkout_path,
            "controller_home_digest": self.controller_home_digest,
            "revision": self.revision, "purpose": self.purpose,
            "registered_at": self.registered_at, "renewed_at": self.renewed_at,
            "expires_at": self.expires_at, "state": self.state,
            "broken_by": self.broken_by, "broken_at": self.broken_at,
            "expired": self.expired(now),
        }

    def stored(self) -> dict:
        value = self.as_mapping(0)
        value.pop("expired")
        return value


def _int(value) -> int | None:
    return value if type(value) is int and 0 <= value < 2**40 else None


def parse_pin(value) -> Pin | None:
    """Validate one stored pin; anything malformed is dropped, never trusted."""
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return None
    holder, revision = value.get("holder"), value.get("revision")
    checkout, home = value.get("checkout_path"), value.get("controller_home_digest")
    purpose, state = value.get("purpose"), value.get("state")
    times = [_int(value.get(k)) for k in ("registered_at", "renewed_at", "expires_at")]
    if (not isinstance(holder, str) or not HOLDER_RE.fullmatch(holder)
            or not isinstance(revision, str) or not REVISION_RE.fullmatch(revision)
            or not isinstance(home, str) or not DIGEST_RE.fullmatch(home)
            or not isinstance(checkout, str) or not 0 < len(checkout) <= MAX_CHECKOUT
            or not _printable(checkout) or not _secret_free(checkout)
            or not isinstance(purpose, str) or len(purpose) > MAX_PURPOSE
            or not _printable(purpose) or not _secret_free(purpose)
            or state not in (ACTIVE, BROKEN) or None in times):
        return None
    registered, renewed, expires = times
    if not registered <= renewed <= expires or expires - renewed > MAX_TTL_SECONDS:
        return None
    broken_by, broken_at = value.get("broken_by"), value.get("broken_at")
    if state == BROKEN:
        if (not isinstance(broken_by, str) or not HOLDER_RE.fullmatch(broken_by)
                or _int(broken_at) is None):
            return None
    else:
        broken_by = broken_at = None
    return Pin(holder, checkout, home, revision, purpose, registered, renewed, expires,
               state, broken_by, broken_at)


# The remote side. Fixed text; its only inputs are the remote Sandbox home and
# one base64 JSON request. Writes are atomic (temp file + rename, 0600) under
# an exclusive flock; expired pins are removed on every write.
#
# Runtime replacement (FR-015) is serialized by that same flock: the install
# command runs under INSTALL_GATE, which takes the lock, refuses over any
# binding pin for another revision, and holds the lock until the install
# process itself exits. Every pin program, including the ones older
# controllers send, takes this lock, so no pin can be registered between the
# check and the end of the install, two installs never overlap, and a lost
# SSH connection cannot release it early. ``.replacing.json`` is the fence an
# earlier revision of this program wrote; a live one is still honoured.
PROGRAM = r'''
import base64, fcntl, json, os, re, sys, time
home = sys.argv[1]
request = json.loads(base64.b64decode(sys.argv[2]).decode())
HOLDER = re.compile(r"h-[0-9a-f]{16}")
CAPACITY = 64
FENCE = ".replacing.json"
root = os.path.join(home, "runtime", "remote-pins")
now = int(time.time())
if request.get("action") == "list":
    # Read-only: never create the directory or its lock file.
    if not os.path.isdir(root):
        print(json.dumps({"ok": True, "action": "list", "now": now, "pins": []}))
        raise SystemExit(0)
    try:
        lock = os.open(os.path.join(root, ".lock"), os.O_RDONLY)
        fcntl.flock(lock, fcntl.LOCK_SH)
    except FileNotFoundError:
        pass
else:
    os.makedirs(root, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    lock = os.open(os.path.join(root, ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)

def path(holder):
    if not isinstance(holder, str) or not HOLDER.fullmatch(holder):
        raise SystemExit(3)
    return os.path.join(root, holder + ".json")

def read(holder):
    try:
        with open(path(holder)) as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None

def put(target, value):
    temp = target + ".tmp"
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, sort_keys=True)
    os.replace(temp, target)

def write(value):
    put(path(value.get("holder")), value)

def fence():
    try:
        with open(os.path.join(root, FENCE)) as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or type(value.get("expires_at")) is not int \
            or value["expires_at"] <= now:
        return None
    return value

def binding(value):
    expires = value.get("expires_at")
    return value.get("state") == "active" and type(expires) is int and expires > now

def every():
    out = []
    for name in sorted(os.listdir(root)):
        if name.endswith(".json") and HOLDER.fullmatch(name[:-5]):
            value = read(name[:-5])
            if value is not None:
                out.append(value)
    return out

def sweep():
    for value in every():
        expires = value.get("expires_at")
        if type(expires) is int and expires <= now:
            try:
                os.unlink(path(value.get("holder")))
            except OSError:
                pass

action = request.get("action")
result = {"ok": True, "action": action, "now": now}
if action == "list":
    result["pins"] = every()
elif action == "register":
    sweep()
    pin = request["pin"]
    live = fence()
    if live is not None and live.get("target") != pin.get("revision"):
        result["refused"] = "runtime_replacement_in_progress"
    elif read(pin.get("holder")) is None and len(every()) >= CAPACITY:
        result["refused"] = "pin_capacity_reached"
    else:
        write(pin)
        result["pin"] = read(pin["holder"])
elif action == "release":
    sweep()
    try:
        os.unlink(path(request["holder"]))
        result["released"] = True
    except FileNotFoundError:
        result["released"] = False
elif action == "break":
    sweep()
    broken = []
    for holder in request["holders"]:
        value = read(holder)
        if value is not None and value.get("state") == "active":
            value["state"] = "broken"
            value["broken_by"] = request["by"]
            value["broken_at"] = now
            write(value)
            broken.append(holder)
    result["broken"] = broken
else:
    raise SystemExit(2)
print(json.dumps(result, sort_keys=True))
'''


# Shared by INSTALL_GATE and ACTIVE_JOBS_PROGRAM: in the side-by-side release
# layout (``sb-src`` is a symlink, sandbox.remote_runtime.releases) a service
# restart can only kill a job supervisor that sits in the service's cgroup.
SERVICE_SUPERVISORS = r'''def service_supervisors():
    """Job ids whose supervisor runs inside the control service's cgroup."""
    found = set()
    for pid in os.listdir("/proc") if os.path.isdir("/proc") else ():
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cgroup" % pid) as handle:
                cgroup = handle.read()
            with open("/proc/%s/cmdline" % pid, "rb") as handle:
                args = handle.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        if "/sandbox-mcp-remote.service" not in cgroup or "sandbox.jobs.supervisor" not in args:
            continue
        for arg in args:
            for part in arg.split("/"):
                if re.fullmatch(r"[0-9a-f]{32}", part):
                    found.add(part)
    return found
'''


# Wraps one runtime install command (``sh -c``) on the remote. Exit 45: the
# lock is held by another install (or a live legacy fence names another
# target); exit 46: binding pins for another revision, listed as one JSON line
# on stdout. Otherwise the child's status. The child does not inherit the lock
# descriptor, so a process it leaves running never holds the lock; SIGHUP is
# ignored so a dropped connection neither kills the install halfway nor
# releases the lock while it runs.
#
# Running jobs: a restart replaces the runtime under every unfinished
# server-side job (a nested server-cli host apply then fails with its rollback
# withheld). After the pin check the gate opens the job registry with
# ``BEGIN IMMEDIATE``, the write lock every admission takes
# (``JobRepository.accept``), and refuses with exit 47 over any job whose
# lifecycle is in ACTIVE_JOB_LIFECYCLES and whose id the operator did not name
# (``--ignore-job``), listed as one JSON line; exit 48 when that lock cannot be
# taken. Queued jobs do not block: while the lock is held they cannot start,
# and otherwise (scoped release, below) they start in their own scopes; an
# accepted job whose launch dies with the old service is failed by
# JobService.reconcile_startup after its launch grace. A child status of 45-48 is reported as 1, so an
# install failure is never read as a gate refusal. With no such job it keeps the write lock, so no
# job is admitted, until the install creates the file named by
# ``SANDBOX_JOBS_GATE_RELEASE`` (after it has stopped the old service and
# before it starts any service, whose startup writes the registry) or exits.
# The gate removes that file once the lock is released.
# In the release layout, when both sb-src and the running service's own
# release launch supervisors in their own scopes (sandbox/jobs/scope.py), the
# lock is released right after the check instead: nothing admitted later can die with the restart, and a
# lock held through the old service's stop fails admissions and running
# supervisors' registry writes once it outlasts their busy timeout.
INSTALL_GATE = r'''
import base64, fcntl, json, os, re, shutil, signal, sqlite3, subprocess, sys, tempfile, threading, time
home, target = sys.argv[1], sys.argv[2]
acknowledged = set(json.loads(base64.b64decode(sys.argv[3]).decode()))
wait, inner = float(sys.argv[4]), sys.argv[5]
ignored = set(json.loads(base64.b64decode(sys.argv[6]).decode())) if len(sys.argv) > 6 else set()
prepare = sys.argv[7] if len(sys.argv) > 7 else ""
HOLDER = re.compile(r"h-[0-9a-f]{16}")
if prepare:
    # Build the release before any lock: the old service keeps serving and
    # submissions keep flowing while venvs are built.
    built = subprocess.run(["sh", "-c", prepare], stdin=subprocess.DEVNULL,
                           close_fds=True).returncode
    if built != 0:
        raise SystemExit(1 if built in (45, 46, 47, 48) else built)
root = os.path.join(home, "runtime", "remote-pins")
os.makedirs(root, mode=0o700, exist_ok=True)
os.chmod(root, 0o700)
lock = os.open(os.path.join(root, ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
deadline = time.monotonic() + wait
while True:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except BlockingIOError:
        if time.monotonic() >= deadline:
            raise SystemExit(45)
        time.sleep(0.2)
now = int(time.time())
try:
    with open(os.path.join(root, ".replacing.json")) as handle:
        fence = json.load(handle)
except (OSError, ValueError):
    fence = None
if isinstance(fence, dict) and type(fence.get("expires_at")) is int \
        and fence["expires_at"] > now and fence.get("target") != target:
    raise SystemExit(45)
blocking = []
for name in sorted(os.listdir(root)):
    if not (name.endswith(".json") and HOLDER.fullmatch(name[:-5])):
        continue
    try:
        with open(os.path.join(root, name)) as handle:
            pin = json.load(handle)
    except (OSError, ValueError):
        continue
    expires = pin.get("expires_at") if isinstance(pin, dict) else None
    if (isinstance(pin, dict) and pin.get("state") == "active" and type(expires) is int
            and expires > now and pin.get("revision") != target
            and name[:-5] not in acknowledged):
        blocking.append(name[:-5])
if blocking:
    print(json.dumps({"pin_gate": "blocked", "blocking": blocking}))
    raise SystemExit(46)
ACTIVE = ("accepted", "running", "cancelling")
registry = os.path.join(home, "runtime", "jobs", "registry.sqlite3")
hold = None

%%SERVICE_SUPERVISORS%%
def serving_scoped():
    """Whether the control service now running was started from a release that
    launches supervisors in scopes (its cwd, not the sb-src link, which can
    already point elsewhere). No service: nothing to lose. Unreadable: no."""
    try:
        pid = subprocess.run(
            ["systemctl", "--user", "show", "-p", "MainPID", "--value", "sandbox-mcp-remote.service"],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL).stdout.strip()
    except FileNotFoundError:
        return True
    except Exception:
        return False
    if pid == "0":
        return True
    if not pid.isdigit():
        return False
    try:
        cwd = os.readlink("/proc/%s/cwd" % pid)
    except OSError:
        return False
    return os.path.isfile(os.path.join(cwd, "sandbox", "jobs", "scope.py"))

if os.path.exists(registry):
    try:
        hold = sqlite3.connect(registry, timeout=15, isolation_level=None,
                               check_same_thread=False)
        hold.execute("BEGIN IMMEDIATE")
        rows = hold.execute(
            "SELECT job_id, kind, lifecycle FROM jobs WHERE lifecycle IN (?,?,?) "
            "ORDER BY accepted_at, job_id", ACTIVE).fetchall()
    except sqlite3.Error:
        print(json.dumps({"jobs_gate": "unavailable"}))
        raise SystemExit(48)
    releases = os.path.islink(os.path.join(home, "sb-src"))
    if releases:
        # Release layout: supervisors run in their own scopes and from their
        # own release directories, so a restart loses only a supervisor that
        # still sits in the service's cgroup.
        exposed = service_supervisors()
        rows = [row for row in rows if row[0] in exposed]
    rows = [row for row in rows if row[0] not in ignored]
    if rows:
        hold.rollback()
        hold.close()
        print(json.dumps({"jobs_gate": "blocked", "count": len(rows), "jobs": [
            {"job_id": r[0], "kind": r[1], "lifecycle": r[2]} for r in rows[:50]]}))
        raise SystemExit(47)
    if releases and os.path.isfile(os.path.join(home, "sb-src", "sandbox", "jobs", "scope.py")) \
            and serving_scoped():
        # The serving release launches every supervisor in its own scope, so
        # no job admitted from here on can die with the restart. Holding the
        # lock through the old service's stop would only fail admissions and
        # registry writes of running supervisors with "database is locked".
        hold.rollback()
        hold.close()
        hold = None
signal.signal(signal.SIGHUP, signal.SIG_IGN)
gate_dir = tempfile.mkdtemp(prefix=".jobs-gate-", dir=root)
release = os.path.join(gate_dir, "release")
done = threading.Event()

def watch():
    while not os.path.exists(release) and not done.is_set():
        time.sleep(0.05)
    if hold is not None:
        hold.rollback()
        hold.close()
    try:
        os.unlink(release)
    except OSError:
        pass

watcher = threading.Thread(target=watch, daemon=True)
watcher.start()
env = dict(os.environ, SANDBOX_JOBS_GATE_RELEASE=release)
try:
    status = subprocess.run(["sh", "-c", inner], close_fds=True, env=env).returncode
finally:
    done.set()
    watcher.join()
    shutil.rmtree(gate_dir, ignore_errors=True)
raise SystemExit(1 if status in (45, 46, 47, 48) else status)
'''.replace("%%SERVICE_SUPERVISORS%%", SERVICE_SUPERVISORS)
INSTALL_BUSY_EXIT = 45
INSTALL_BLOCKED_EXIT = 46
JOBS_ACTIVE_EXIT = 47
JOBS_GATE_UNAVAILABLE_EXIT = 48
# The non-terminal members of sandbox.jobs.models.Lifecycle except queued
# (tests/test_remote_runtime_pins.py); the remote programs carry literal copies.
ACTIVE_JOB_LIFECYCLES = ("accepted", "running", "cancelling")
MAX_LISTED_JOBS = 50
JOB_ID_RE = re.compile(r"[0-9a-f]{32}")
_JOB_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")

# Read-only listing for plans and the early confirm check. The authoritative
# check is the one INSTALL_GATE makes under the admission lock.
ACTIVE_JOBS_PROGRAM = r'''
import json, os, re, sqlite3, sys, urllib.parse
ACTIVE = ("accepted", "running", "cancelling")
registry = os.path.join(sys.argv[1], "runtime", "jobs", "registry.sqlite3")
blocking_only = len(sys.argv) > 2 and sys.argv[2] == "blocking"
%%SERVICE_SUPERVISORS%%
if not os.path.exists(registry):
    print(json.dumps({"count": 0, "jobs": []}))
    raise SystemExit(0)
connection = sqlite3.connect("file:" + urllib.parse.quote(registry) + "?mode=ro",
                             uri=True, timeout=5)
connection.execute("PRAGMA query_only=ON")
rows = connection.execute(
    "SELECT job_id, kind, lifecycle FROM jobs WHERE lifecycle IN (?,?,?) "
    "ORDER BY accepted_at, job_id", ACTIVE).fetchall()
if blocking_only and os.path.islink(os.path.join(sys.argv[1], "sb-src")):
    exposed = service_supervisors()
    rows = [row for row in rows if row[0] in exposed]
print(json.dumps({"count": len(rows), "jobs": [
    {"job_id": r[0], "kind": r[1], "lifecycle": r[2]} for r in rows[:50]]}))
'''.replace("%%SERVICE_SUPERVISORS%%", SERVICE_SUPERVISORS)
# Shell for the gated install: creates the release file and waits until the
# gate removed it, i.e. released the job admission lock. The flag clears only
# then, so a failed release is retried by rollback, which then fails closed.
RELEASE_JOBS_GATE_SH = (
    "jobs_gate_held=1; release_jobs_gate() { if test \"$jobs_gate_held\" = 1; then "
    # printf, not ':': a failed redirection on a special builtin exits sh.
    "printf '' > \"$SANDBOX_JOBS_GATE_RELEASE\" || return 1; n=0; "
    "while test -e \"$SANDBOX_JOBS_GATE_RELEASE\"; do n=$((n+1)); "
    "test \"$n\" -le 200 || return 1; sleep 0.05; done; jobs_gate_held=0; fi; }; ")
JOBS_ACTIVE = "remote_jobs_active"
JOBS_GATE_UNAVAILABLE = "remote_jobs_gate_unavailable"


def parse_active_jobs(stdout, key=None):
    """``(count, jobs)`` from one program line, or None when it is malformed.

    ``key`` selects the gate's refusal line (``jobs_gate == "blocked"``).
    """
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", "replace")
    for line in (stdout or "").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if not isinstance(value, dict) or (key is not None and value.get("jobs_gate") != key):
            continue
        count, listed = value.get("count"), value.get("jobs")
        if type(count) is not int or count < 0 or not isinstance(listed, list) \
                or len(listed) > MAX_LISTED_JOBS or len(listed) > count:
            return None
        jobs = []
        for job in listed:
            if not isinstance(job, dict) or not JOB_ID_RE.fullmatch(str(job.get("job_id"))):
                return None
            words = {name: job.get(name) if isinstance(job.get(name), str)
                     and _JOB_WORD_RE.fullmatch(job.get(name)) else "unknown"
                     for name in ("kind", "lifecycle")}
            jobs.append({"job_id": job["job_id"], **words})
        return count, jobs
    return None


def jobs_active_error(count: int, jobs: list) -> PinError:
    shown = ", ".join(f"{job['job_id']} ({job['kind']}, {job['lifecycle']})" for job in jobs)
    more = f" and {count - len(jobs)} more" if count > len(jobs) else ""
    error = PinError(JOBS_ACTIVE,
                     f"{count} server-side job(s) have not finished on the remote: {shown}{more}; "
                     "a migrate restarts the service and replaces the runtime under them. "
                     "Wait for them to end (`sb job-list --remote NAME --active-only`) "
                     "or cancel them, then rerun; for a job whose supervisor is gone and "
                     "that cannot be cancelled, name it with --ignore-job JOB_ID")
    error.jobs, error.job_count = jobs, count
    return error


def list_active_jobs(entry: dict, ssh_run: Callable | None = None, *, blocking: bool = False):
    """``(count, jobs)`` of unfinished jobs on the remote, read-only.

    ``blocking`` keeps only the jobs the install gate would refuse over (all
    of them in the old layout; in the release layout only supervisors left in
    the service's cgroup). Raises PinError(JOBS_GATE_UNAVAILABLE) when they
    cannot be read.
    """
    if ssh_run is None:
        from sandbox.core._remote import ssh_run as default_ssh_run
        ssh_run = default_ssh_run
    command = (f"python3 -c {shlex.quote(ACTIVE_JOBS_PROGRAM)} "
               f"\"${{SANDBOX_HOME:-$HOME/sandbox}}\"" + (" blocking" if blocking else ""))
    try:
        result = ssh_run(entry, command, timeout=TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - transport detail is never forwarded
        raise PinError(JOBS_GATE_UNAVAILABLE, "remote jobs could not be listed") from exc
    parsed = parse_active_jobs(result.stdout) if result.returncode == 0 else None
    if parsed is None:
        raise PinError(JOBS_GATE_UNAVAILABLE, "remote jobs could not be listed")
    return parsed


def install_gate_command(target: str, acknowledged, inner: str, *,
                         wait_seconds: float = INSTALL_LOCK_WAIT_SECONDS,
                         ignored_jobs=(), prepare: str = "") -> str:
    """``inner`` wrapped so it runs only under the remote pin lock (FR-015)."""
    if not REVISION_RE.fullmatch(target or "") \
            or not all(HOLDER_RE.fullmatch(h or "") for h in acknowledged or ()) \
            or not all(JOB_ID_RE.fullmatch(j or "") for j in ignored_jobs or ()):
        raise PinError("pin_holder_invalid", "install gate request is invalid")
    acknowledged_b64 = base64.b64encode(
        json.dumps(sorted(set(acknowledged or ()))).encode()).decode()
    ignored_b64 = base64.b64encode(json.dumps(sorted(set(ignored_jobs or ()))).encode()).decode()
    return (f"python3 -c {shlex.quote(INSTALL_GATE)} "
            f"\"${{SANDBOX_HOME:-$HOME/sandbox}}\" {shlex.quote(target)} "
            f"{shlex.quote(acknowledged_b64)} {float(wait_seconds)!r} {shlex.quote(inner)} "
            f"{shlex.quote(ignored_b64)}"
            + (f" {shlex.quote(prepare)}" if prepare else ""))


def install_gate_refusal(returncode: int, stdout) -> PinError | None:
    """The typed refusal for a gated install's exit status, else None."""
    if returncode == JOBS_ACTIVE_EXIT:
        parsed = parse_active_jobs(stdout, key="blocked")
        if parsed is None:
            return PinError(JOBS_GATE_UNAVAILABLE,
                            "the remote refused over unfinished jobs it could not list")
        return jobs_active_error(*parsed)
    if returncode == JOBS_GATE_UNAVAILABLE_EXIT:
        return PinError(JOBS_GATE_UNAVAILABLE,
                        "the remote job registry's admission lock could not be taken; "
                        "nothing was installed")
    if returncode == INSTALL_BUSY_EXIT:
        return PinError(REPLACEMENT_IN_PROGRESS,
                        "another install holds the remote runtime replacement lock")
    if returncode != INSTALL_BLOCKED_EXIT:
        return None
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", "replace")
    blocking = []
    for line in (stdout or "").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("pin_gate") == "blocked":
            blocking = [h for h in value.get("blocking") or ()
                        if isinstance(h, str) and HOLDER_RE.fullmatch(h)]
    error = PinError(PINS_UNACKNOWLEDGED, "pass --break-pin HOLDER for: " + ", ".join(blocking))
    error.blocking = blocking
    return error


def remote_command(request: dict) -> str:
    payload = base64.b64encode(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).decode()
    return (f"python3 -c {shlex.quote(PROGRAM)} "
            f"\"${{SANDBOX_HOME:-$HOME/sandbox}}\" {shlex.quote(payload)}")


class PinStore:
    """Pins on one remote, reached through an injected ``ssh_run``."""

    def __init__(self, entry: dict, ssh_run: Callable | None = None):
        if ssh_run is None:
            from sandbox.core._remote import ssh_run as default_ssh_run
            ssh_run = default_ssh_run
        self.entry = entry
        self._ssh_run = ssh_run

    def _call(self, request: dict) -> dict:
        try:
            result = self._ssh_run(self.entry, remote_command(request), timeout=TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - transport detail is never forwarded
            raise PinError(PINS_UNAVAILABLE, "remote pins are unreachable") from exc
        stdout = getattr(result, "stdout", "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if getattr(result, "returncode", 1) != 0 or not isinstance(stdout, str):
            raise PinError(PINS_UNAVAILABLE, "remote pin program failed")
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise PinError(PINS_UNAVAILABLE, "remote pin program output is invalid") from None
        if not isinstance(value, dict) or value.get("ok") is not True \
                or value.get("action") != request["action"]:
            raise PinError(PINS_UNAVAILABLE, "remote pin program output is invalid")
        return value

    def list(self) -> list[Pin]:
        raw = self._call({"action": "list"}).get("pins")
        if not isinstance(raw, list):
            raise PinError(PINS_UNAVAILABLE, "remote pin list is invalid")
        if len(raw) > MAX_LISTED:
            # Never decide on a partial list: an omitted pin may be the binding one.
            raise PinError(PINS_UNAVAILABLE, "remote pin list exceeds capacity")
        pins = [pin for pin in (parse_pin(item) for item in raw) if pin]
        return sorted(pins, key=lambda pin: pin.holder)

    def get(self, holder: str) -> Pin | None:
        return next((pin for pin in self.list() if pin.holder == holder), None)

    def register(self, pin: Pin) -> Pin:
        if parse_pin(pin.stored()) != pin:
            raise PinError(PIN_UNVERIFIABLE, "pin is invalid")
        result = self._call({"action": "register", "pin": pin.stored()})
        if result.get("refused") in (REPLACEMENT_IN_PROGRESS, PIN_CAPACITY_REACHED):
            raise PinError(result["refused"], "remote refused the pin")
        stored = parse_pin(result.get("pin"))
        if stored != pin:
            raise PinError(PIN_UNVERIFIABLE, "remote did not store the pin")
        return stored

    def release(self, holder: str) -> bool:
        if not HOLDER_RE.fullmatch(holder or ""):
            raise PinError("pin_holder_invalid", "holder is invalid")
        return bool(self._call({"action": "release", "holder": holder}).get("released"))

    def break_pins(self, holders: list[str], by: str) -> list[str]:
        if not HOLDER_RE.fullmatch(by or "") or not all(
                HOLDER_RE.fullmatch(h or "") for h in holders):
            raise PinError("pin_holder_invalid", "holder is invalid")
        broken = self._call({"action": "break", "holders": list(holders), "by": by}).get("broken")
        return [h for h in broken if h in holders] if isinstance(broken, list) else []


def local_holder(checkout: str | os.PathLike | None = None) -> tuple[str, str, str]:
    """``(holder, checkout_path, home_digest)`` for this controller checkout."""
    from sandbox.core._paths import BASE, ROOT
    checkout = os.path.realpath(checkout or ROOT)
    home = os.path.realpath(BASE)
    return holder_identity(home, checkout), checkout, _digest16(home)


def new_pin(revision: str, purpose: str, *, checkout=None, previous: Pin | None = None,
            ttl_seconds: int = DEFAULT_TTL_SECONDS, now: float | None = None) -> Pin:
    holder, checkout_path, home_digest = local_holder(checkout)
    moment = int(time.time() if now is None else now)
    ttl = max(1, min(int(ttl_seconds), MAX_TTL_SECONDS))
    registered = previous.registered_at if previous and previous.binding(moment) \
        and previous.revision == revision else moment
    return Pin(holder, checkout_path, home_digest, revision, purpose[:MAX_PURPOSE],
               registered, moment, moment + ttl)


def strict_gate(entry: dict, status: dict, *, purpose: str, store: PinStore | None = None,
                checkout=None, now: float | None = None,
                recheck: Callable[[], dict] | None = None) -> dict:
    """Apply strict mode to a status envelope before a remote effect.

    The returned envelope's ``compatibility`` is refused unless the installed
    revision is exactly this controller's and the holder's pin was registered
    or renewed. A refused strict invocation registers nothing; a broken pin is
    reported with who broke it and when; an unverifiable pin fails closed
    (FR-008..FR-011).

    ``status`` was read before the pin existed. ``recheck`` re-reads it after
    registration: a runtime replaced in between (the fence was already gone)
    shows a different installed revision, and the new pin is released.
    """
    status = dict(status) if isinstance(status, dict) else {}
    verdict = dict(status.get("compatibility") or {})
    verdict["strict"] = True
    local = status.get("local_runtime_revision")
    installed = status.get("installed_runtime_revision")

    def refuse(reason: str, **extra) -> dict:
        verdict.update(ok=False, reason=reason, **extra)
        status["compatibility"] = verdict
        return status

    store = store or PinStore(entry)
    holder = local_holder(checkout)[0]
    try:
        previous = store.get(holder)
    except PinError:
        previous, reachable = None, False
    else:
        reachable = True
    broken = ({"broken_pin": {"holder": previous.holder, "broken_by": previous.broken_by,
                              "broken_at": previous.broken_at}}
              if previous is not None and previous.state == BROKEN else {})
    if (not isinstance(local, str) or not REVISION_RE.fullmatch(local)
            or local != installed):
        return refuse("strict_requires_exact_revision", **broken)
    if not reachable:
        return refuse(PIN_UNVERIFIABLE, state="unknown")
    try:
        pin = store.register(new_pin(local, purpose, checkout=checkout,
                                     previous=previous, now=now))
    except PinError:
        return refuse(PIN_UNVERIFIABLE, state="unknown")
    if recheck is not None:
        try:
            after = recheck()
        except Exception:  # noqa: BLE001 - transport detail is never forwarded
            after = None
        installed_after = after.get("installed_runtime_revision") \
            if isinstance(after, dict) else None
        if installed_after != local:
            try:
                store.release(pin.holder)
            except PinError:
                pass  # it expires; it names this revision, which is no longer installed
            if installed_after is None:
                return refuse(PIN_UNVERIFIABLE, state="unknown")
            status["installed_runtime_revision"] = installed_after
            status["runtime_revision_state"] = "mismatch"
            return refuse("strict_requires_exact_revision")
    verdict.update(ok=True, reason="strict_pin_held",
                   pin={"holder": pin.holder, "expires_at": pin.expires_at})
    status["compatibility"] = verdict
    return status
