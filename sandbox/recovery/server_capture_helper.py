"""Server-first recovery capture helper (spec 058).

Stdlib only, Python >= 3.8.  ``sandbox.transports.remote_server_capture``
sends this file as ``python3 -c <source> <op> <capture_root> <args...>`` over
the registered SSH transport.  Every op prints one JSON object on stdout and
exits 0; ``read-chunk`` follows its JSON header line with raw archive bytes.
Failures are ``{"ok": false, "code": "<fixed code>"}``.  No op prints a
secret, a path outside the capture root or raw tool diagnostics.

The database password arrives as the first stdin line of ``start`` and lives
only in the detached job's memory; it is never written to disk.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time

SCHEMA = 1
DOCKER = "docker"
WP_CONTAINER = "sandbox-host-amarsonar-bangla-production-wordpress-1"
DB_CONTAINER = "sandbox-host-amarsonar-bangla-production-db-1"
DB_USER = "amarsonar"
DB_NAME = "amarsonar"
WP_ROOT = "/var/www/html"
STEP_SECONDS = 1800
JOB_SECONDS = 3600
CHUNK_MAX = 16 * 1024 * 1024
READ = 1024 * 1024
MAX_SLOTS = 500
MAX_NAMES = 200
LIST_BYTES = 256 * 1024
PHASES = ("preflight", "inventory", "dump", "files", "verify", "archive", "receipt")
KEEP = ("request.json", "state.json", "declarations.json", "promoted.json", "job.lock")
SLOT = re.compile(r"^capture-[0-9a-f]{64}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUEST_ID = re.compile(r"^recovery-[0-9a-f]{64}$")
SET_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SIZE_QUERY = ("SELECT COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) "
              "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")
INVENTORY_QUERY = ("SELECT TABLE_NAME, TABLE_TYPE, TABLE_ROWS "
                   "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")
TABLE_TYPES = {"BASE TABLE": "table", "SYSTEM VERSIONED": "table", "VIEW": "view"}
CREATE_TABLE = re.compile(rb"^CREATE TABLE (?:IF NOT EXISTS )?`((?:[^`]|``)+)`")
VIEW_NAME = re.compile(rb"\bVIEW `((?:[^`]|``)+)` AS\b")


class Refusal(Exception):
    def __init__(self, code, **data):
        Exception.__init__(self, code)
        self.code = code
        self.data = data


class Failure(Exception):
    def __init__(self, code, detail=None):
        Exception.__init__(self, code)
        self.code = code
        self.detail = detail


def emit(payload):
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


# --- owner-only file primitives -------------------------------------------

def ensure_root(root, create):
    if create:
        os.makedirs(root, mode=0o700, exist_ok=True)
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise Refusal("capture_root_invalid")
    if create and stat.S_IMODE(info.st_mode) & 0o077:
        os.chmod(root, 0o700)
    return True


def fsync_dir(directory):
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def create_private(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def write_private(directory, name, data):
    """Create with O_EXCL|O_NOFOLLOW 0600, fsync, then rename into place."""
    temporary = os.path.join(directory, ".%s.%d.tmp" % (name, os.getpid()))
    fd = create_private(temporary)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        os.unlink(temporary)
        raise
    os.close(fd)
    os.rename(temporary, os.path.join(directory, name))
    fsync_dir(directory)


def read_owned(path, limit):
    """Read one owner-only regular file; refuse links, sharing and oversize."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise
    except OSError:
        raise Refusal("record_invalid")
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
                or info.st_size > limit):
            raise Refusal("record_invalid")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(READ, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise Refusal("record_invalid")
        return b"".join(chunks)
    finally:
        os.close(fd)


def load_json(path, limit):
    """Return a JSON object from an owned file, or None when absent/invalid."""
    try:
        value = json.loads(read_owned(path, limit).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, Refusal):
        return None
    return value if isinstance(value, dict) else None


def owned_size(path):
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        return None
    return info.st_size


def tree_bytes(path):
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        return info.st_size
    total = 0
    for base, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                total += os.lstat(os.path.join(base, name)).st_size
            except OSError:
                pass
    return total


def remove_path(path):
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    size = tree_bytes(path)
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path, ignore_errors=True)
    else:
        os.unlink(path)
    return size


def try_lock(path, create):
    flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def lock_free(path):
    """Probe a lock without creating or modifying anything."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def hash_file(path):
    digest, size = hashlib.sha256(), 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        while True:
            chunk = os.read(fd, READ)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest(), size


# --- slot facts ------------------------------------------------------------

def slot_dir(root, slot):
    if not isinstance(slot, str) or not SLOT.match(slot):
        raise Refusal("request_invalid")
    return os.path.join(root, slot)


def valid_receipt(receipt, request, archive_size):
    if not isinstance(receipt, dict) or not isinstance(request, dict):
        return False
    members = receipt.get("members")
    numbers = (receipt.get("started_at"), receipt.get("completed_at"))
    return (receipt.get("schema_version") == SCHEMA
            and receipt.get("request_id") == request.get("request_id")
            and receipt.get("backup_operation_id") == request.get("backup_operation_id")
            and isinstance(archive_size, int) and archive_size > 0
            and receipt.get("archive_size") == archive_size
            and isinstance(receipt.get("archive_sha256"), str)
            and bool(HEX64.match(receipt["archive_sha256"]))
            and isinstance(members, list) and len(members) == 2
            and all(isinstance(item, dict) and isinstance(item.get("name"), str)
                    and isinstance(item.get("sha256"), str) and HEX64.match(item["sha256"])
                    and isinstance(item.get("size"), int) for item in members)
            and isinstance(receipt.get("inventory"), dict)
            and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in numbers)
            and numbers[1] >= numbers[0])


def receipt_summary(receipt):
    inventory = receipt.get("inventory") or {}
    return {
        "archive_sha256": receipt.get("archive_sha256"),
        "archive_size": receipt.get("archive_size"),
        "members": receipt.get("members"),
        "declarations_sha256": receipt.get("declarations_sha256"),
        "inventory_summary": inventory.get("summary"),
        "started_at": receipt.get("started_at"),
        "completed_at": receipt.get("completed_at"),
    }


def facts(root, slot):
    directory = slot_dir(root, slot)
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise Refusal("record_invalid")
    request = load_json(os.path.join(directory, "request.json"), 16 * 1024)
    state = load_json(os.path.join(directory, "state.json"), 64 * 1024)
    archive_size = owned_size(os.path.join(directory, "archive.tar"))
    receipt = load_json(os.path.join(directory, "receipt.json"), 1024 * 1024)
    is_valid = valid_receipt(receipt, request, archive_size)
    residue = 0
    for name in os.listdir(directory):
        if name in KEEP or (is_valid and name in ("archive.tar", "receipt.json")):
            continue
        residue += tree_bytes(os.path.join(directory, name))
    return {
        "slot": slot, "request": request, "state": state,
        "lock_free": lock_free(os.path.join(directory, "job.lock")),
        "receipt_valid": is_valid,
        "receipt": receipt_summary(receipt) if is_valid else None,
        "archive_size": archive_size, "residue_bytes": residue,
        "promoted": load_json(os.path.join(directory, "promoted.json"), 4096),
    }


def derive(item):
    """Same rules as ``sandbox.recovery.server_capture.derive_state``."""
    state = (item.get("state") or {}).get("state")
    if state is None:
        return "queued" if not item.get("lock_free") else "incomplete"
    if state in ("retired", "failed"):
        return state
    if state == "complete":
        return "complete" if item.get("receipt_valid") else "incomplete"
    if state in ("queued", "running"):
        return "incomplete" if item.get("lock_free") else state
    return "incomplete"


def review_state(item):
    derived = derive(item)
    if derived == "complete" and item.get("promoted"):
        return "promoted"
    return derived


def active_backup_id(root):
    for name in sorted(os.listdir(root)):
        if SLOT.match(name) and not lock_free(os.path.join(root, name, "job.lock")):
            request = load_json(os.path.join(root, name, "request.json"), 16 * 1024)
            return request.get("backup_id") if request else None
    return None


# --- ops -------------------------------------------------------------------

def existing_result(root, slot, request):
    directory = slot_dir(root, slot)
    if not os.path.lexists(directory):
        return None
    stored = None
    for _ in range(40):
        stored = load_json(os.path.join(directory, "request.json"), 16 * 1024)
        if stored is not None:
            break
        time.sleep(0.05)
    if stored is None:
        raise Refusal("capture_in_progress", active_backup_id=request.get("backup_id"))
    if stored.get("request_id") != request.get("request_id"):
        raise Refusal("capture_binding_conflict")
    return {"ok": True, "existing": True, "status": facts(root, slot)}


def detach_supported():
    executable = shutil.which("loginctl")
    if not executable:
        return True
    try:
        result = subprocess.run([executable, "show", "-p", "KillUserProcesses"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    return b"KillUserProcesses=yes" not in result.stdout


def validate_request(request, slot):
    if (not isinstance(request, dict) or request.get("schema_version") != SCHEMA
            or request.get("slot") != slot
            or not isinstance(request.get("request_id"), str)
            or not REQUEST_ID.match(request["request_id"])
            or not isinstance(request.get("backup_id"), str)
            or not SET_ID.match(request["backup_id"])
            or request.get("backup_operation_id") != request["backup_id"]
            or not isinstance(request.get("declarations_sha256"), str)
            or not HEX64.match(request["declarations_sha256"])):
        raise Refusal("request_invalid")


def op_start(root, slot, request_text):
    if len(request_text) > 16 * 1024:
        raise Refusal("request_invalid")
    try:
        request = json.loads(request_text)
    except ValueError:
        raise Refusal("request_invalid")
    validate_request(request, slot)
    payload = sys.stdin.buffer.read(64 * 1024 + 4096 + 2)
    password, _, declarations = payload.partition(b"\n")
    password = password.rstrip(b"\r")
    if not password:
        raise Refusal("missing_database_credential")
    if not declarations or len(declarations) > 64 * 1024:
        raise Refusal("request_invalid")
    try:
        if not isinstance(json.loads(declarations.decode("utf-8")), dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        raise Refusal("request_invalid")
    if hashlib.sha256(declarations).hexdigest() != request["declarations_sha256"]:
        raise Refusal("request_invalid")
    ensure_root(root, True)
    directory = slot_dir(root, slot)
    active = None
    while active is None:
        existing = existing_result(root, slot, request)
        if existing:
            return existing
        active = try_lock(os.path.join(root, "active.lock"), True)
        if active is None:
            give_up = time.monotonic() + 2
            while time.monotonic() < give_up:
                existing = existing_result(root, slot, request)
                if existing:
                    return existing
                other = active_backup_id(root)
                if other and other != request["backup_id"]:
                    raise Refusal("capture_in_progress", active_backup_id=other)
                time.sleep(0.05)
            raise Refusal("capture_in_progress", active_backup_id=active_backup_id(root))
        if os.path.lexists(directory):
            os.close(active)
            active = None
    try:
        if not detach_supported():
            raise Refusal("detach_unsupported")
        os.mkdir(directory, 0o700)
        job = try_lock(os.path.join(directory, "job.lock"), True)
        accepted = time.time()
        stored = dict(request, accepted_at=accepted)
        write_private(directory, "request.json", canonical(stored))
        write_private(directory, "declarations.json", declarations)
        write_private(directory, "state.json", canonical({
            "state": "queued", "phase": None, "accepted_at": accepted,
            "started_at": None, "ended_at": None, "reason": None, "detail": None}))
    except BaseException:
        os.close(active)
        raise
    sys.stdout.flush()
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            if os.fork() != 0:
                os._exit(0)
            devnull = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(devnull, fd)
            run_job(root, slot, stored, password, accepted)
        finally:
            os._exit(0)
    os.waitpid(pid, 0)
    os.close(job)
    os.close(active)
    return {"ok": True, "existing": False, "state": "queued", "phase": None,
            "accepted_at": accepted}


def op_status(root, slot):
    slot_dir(root, slot)
    if not ensure_root(root, False):
        raise Refusal("capture_not_found")
    item = facts(root, slot)
    if item is None:
        raise Refusal("capture_not_found")
    return dict(item, ok=True)


def op_list(root):
    slots, legacy, truncated = [], [], False
    if ensure_root(root, False):
        for name in sorted(os.listdir(root)):
            if not SLOT.match(name):
                continue
            if len(slots) >= MAX_SLOTS:
                truncated = True
                break
            item = facts(root, name)
            if item is None:
                continue
            request = item["request"] or {}
            state = item["state"] or {}
            receipt = item["receipt"] or {}
            slots.append({
                "slot": name, "backup_id": request.get("backup_id"),
                "request_id": request.get("request_id"),
                "accepted_at": request.get("accepted_at"),
                "state": state.get("state"), "phase": state.get("phase"),
                "reason": state.get("reason"), "lock_free": item["lock_free"],
                "receipt_valid": item["receipt_valid"],
                "archive_size": item["archive_size"],
                "archive_sha256": receipt.get("archive_sha256"),
                "completed_at": receipt.get("completed_at"),
                "residue_bytes": item["residue_bytes"],
                "promoted": bool(item["promoted"]),
            })
    controller = os.path.join(os.path.dirname(root.rstrip("/")), "recovery-controller")
    try:
        names = sorted(os.listdir(controller))
    except OSError:
        names = []
    for name in names:
        if name.endswith(".tar"):
            size = owned_size(os.path.join(controller, name))
            if size is not None:
                legacy.append({"name": name, "size": size})
            if len(legacy) >= MAX_SLOTS:
                truncated = True
                break
    result = {"ok": True, "slots": slots, "legacy": legacy, "truncated": truncated}
    while len(canonical(result)) > LIST_BYTES and (result["slots"] or result["legacy"]):
        (result["legacy"] if result["legacy"] else result["slots"]).pop()
        result["truncated"] = True
    return result


def require(root, slot):
    slot_dir(root, slot)
    if not ensure_root(root, False):
        raise Refusal("capture_not_found")
    item = facts(root, slot)
    if item is None:
        raise Refusal("capture_not_found")
    return item


def op_read_receipt(root, slot):
    item = require(root, slot)
    if not item["receipt_valid"]:
        raise Refusal("capture_not_complete")
    return {"ok": True, "receipt": load_json(os.path.join(root, slot, "receipt.json"), 1024 * 1024)}


def op_read_declaration(root, slot):
    require(root, slot)
    try:
        raw = read_owned(os.path.join(root, slot, "declarations.json"), 64 * 1024)
        return {"ok": True, "text": raw.decode("utf-8")}
    except (OSError, UnicodeDecodeError):
        raise Refusal("record_invalid")


def op_read_chunk(root, slot, offset, length):
    try:
        offset, length = int(offset), int(length)
    except ValueError:
        raise Refusal("request_invalid")
    item = require(root, slot)
    if not item["receipt_valid"]:
        raise Refusal("capture_not_complete")
    size = item["archive_size"]
    if offset < 0 or offset >= size or length < 1 or length > CHUNK_MAX:
        raise Refusal("request_invalid")
    fd = os.open(os.path.join(root, slot, "archive.tar"), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        wanted, parts = min(length, size - offset), []
        while wanted:
            chunk = os.read(fd, min(READ, wanted))
            if not chunk:
                break
            parts.append(chunk)
            wanted -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(parts)
    header = {"ok": True, "offset": offset, "length": len(data),
              "sha256": hashlib.sha256(data).hexdigest()}
    sys.stdout.buffer.write(canonical(header) + b"\n")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    return None


def op_mark_promoted(root, slot, marker_text):
    if len(marker_text) > 4096:
        raise Refusal("request_invalid")
    try:
        marker = json.loads(marker_text)
    except ValueError:
        raise Refusal("request_invalid")
    item = require(root, slot)
    if derive(item) != "complete":
        raise Refusal("capture_not_complete")
    if (not isinstance(marker, dict) or marker.get("request_id") != item["request"].get("request_id")
            or not isinstance(marker.get("set_id"), str) or not SET_ID.match(marker["set_id"])
            or not isinstance(marker.get("ciphertext_sha256"), str)
            or not HEX64.match(marker["ciphertext_sha256"])
            or not isinstance(marker.get("promoted_at"), str)):
        raise Refusal("request_invalid")
    if item["promoted"]:
        return {"ok": True, "existing": True}
    write_private(os.path.join(root, slot), "promoted.json",
                  canonical(dict(marker, schema_version=SCHEMA)))
    return {"ok": True, "existing": False}


def op_check_integrity(root, slot):
    """Re-hash the server archive; record ``integrity_mismatch`` when it drifted."""
    item = require(root, slot)
    if derive(item) != "complete":
        raise Refusal("capture_not_complete")
    directory = os.path.join(root, slot)
    digest, size = hash_file(os.path.join(directory, "archive.tar"))
    receipt = item["receipt"]
    if digest == receipt["archive_sha256"] and size == receipt["archive_size"]:
        return {"ok": True, "mismatch": False}
    lock = try_lock(os.path.join(directory, "job.lock"), True)
    if lock is None:
        raise Refusal("capture_in_progress")
    try:
        state = dict(item["state"] or {}, state="failed", reason="integrity_mismatch",
                     failed_at=time.time())
        write_private(directory, "state.json", canonical(state))
    finally:
        os.close(lock)
    return {"ok": True, "mismatch": True}


def op_retire(root, slot, plan_text):
    if len(plan_text) > 4096:
        raise Refusal("request_invalid")
    try:
        plan = json.loads(plan_text)
    except ValueError:
        raise Refusal("request_invalid")
    if not isinstance(plan, dict):
        raise Refusal("request_invalid")
    item = require(root, slot)
    current = review_state(item)
    if current not in ("promoted", "failed", "incomplete"):
        raise Refusal("not_retirable", state=current)
    directory = os.path.join(root, slot)
    lock = try_lock(os.path.join(directory, "job.lock"), True)
    if lock is None:
        raise Refusal("not_retirable", state="running")
    try:
        item = facts(root, slot)
        receipt = item["receipt"] or {}
        observed = {"state": review_state(item), "archive_sha256": receipt.get("archive_sha256"),
                    "archive_size": item["archive_size"]}
        if any(plan.get(key) != value for key, value in observed.items()):
            raise Refusal("retire_candidate_changed")
        removed = 0
        for name in sorted(os.listdir(directory)):
            if name not in KEEP:
                removed += remove_path(os.path.join(directory, name))
        retired_at = time.time()
        prior = item["state"] or {}
        write_private(directory, "state.json", canonical({
            "state": "retired", "retired_at": retired_at, "previous_state": observed["state"],
            "reason": prior.get("reason"), "accepted_at": prior.get("accepted_at"),
            "started_at": prior.get("started_at"), "ended_at": prior.get("ended_at"),
            "phase": prior.get("phase"), "detail": None}))
    finally:
        os.close(lock)
    return {"ok": True, "retired_at": retired_at, "removed_bytes": removed}


# --- detached capture job --------------------------------------------------

def limit_from_env(name, default):
    """Tests may tighten (never loosen) the bounds through the environment."""
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if 0 < value < default else default


def remaining(deadline, step):
    left = deadline - time.monotonic()
    if left <= 0:
        raise Failure("capture_timeout")
    return min(step, left)


def docker(*args):
    return [DOCKER] + list(args)


def mariadb(query):
    return docker("exec", "-e", "MYSQL_PWD", DB_CONTAINER, "mariadb", "-N", "-B",
                  "-u", DB_USER, "-e", query, DB_NAME)


def query(argv, env, deadline, step, code):
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, env=env,
                                timeout=remaining(deadline, step))
    except subprocess.TimeoutExpired:
        raise Failure("capture_timeout")
    except OSError:
        raise Failure(code)
    if result.returncode != 0:
        raise Failure(code)
    return result.stdout


def run_to_file(argv, path, env, deadline, step, code):
    fd = create_private(path)
    try:
        result = subprocess.run(argv, stdout=fd, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, env=env,
                                timeout=remaining(deadline, step))
        os.fsync(fd)
    except subprocess.TimeoutExpired:
        raise Failure("capture_timeout")
    except OSError:
        raise Failure(code)
    finally:
        os.close(fd)
    if result.returncode != 0 or not os.path.getsize(path):
        raise Failure(code)


def hash_stream(argv, env, deadline, step, code):
    """Hash a second tar stream through a pipe; nothing is written to disk."""
    timeout = remaining(deadline, step)
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, env=env)
    except OSError:
        raise Failure(code)
    expired = []

    def kill():
        expired.append(True)
        process.kill()

    timer = threading.Timer(timeout, kill)
    timer.start()
    digest, size = hashlib.sha256(), 0
    try:
        while True:
            chunk = process.stdout.read(READ)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        process.wait()
    finally:
        timer.cancel()
        process.stdout.close()
    if expired:
        raise Failure("capture_timeout")
    if process.returncode != 0:
        raise Failure(code)
    return digest.hexdigest(), size


def parse_inventory(output):
    tables = {}
    for line in output.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[1] not in TABLE_TYPES or not parts[0]:
            continue
        rows = None if parts[2] in ("NULL", "") else int(parts[2])
        tables[parts[0]] = {"name": parts[0], "type": TABLE_TYPES[parts[1]], "rows_estimate": rows}
    ordered = [tables[name] for name in sorted(tables)]
    return {
        "taken_at": time.time(), "tables": ordered, "dump_matches": True,
        "summary": {
            "table_count": sum(1 for item in ordered if item["type"] == "table"),
            "view_count": sum(1 for item in ordered if item["type"] == "view"),
            "rows_estimate_total": sum(item["rows_estimate"] or 0 for item in ordered),
        },
    }


def dump_names(path):
    """Collect table and view names with bounded line reads."""
    names, at_start = set(), True
    with open(path, "rb") as stream:
        while True:
            line = stream.readline(READ)
            if not line:
                break
            if at_start:
                match = CREATE_TABLE.match(line)
                if match:
                    names.add(match.group(1).replace(b"``", b"`").decode("utf-8", "replace"))
                if line.startswith((b"/*!50001 ", b"CREATE ")) and b"VIEW `" in line:
                    for match in VIEW_NAME.finditer(line):
                        names.add(match.group(1).replace(b"``", b"`").decode("utf-8", "replace"))
            at_start = line.endswith(b"\n")
    return names


def write_state(directory, state):
    write_private(directory, "state.json", canonical(state))


def run_job(root, slot, request, password, accepted):
    directory = os.path.join(root, slot)
    work = os.path.join(directory, "work")
    step = limit_from_env("SANDBOX_CAPTURE_STEP_SECONDS", STEP_SECONDS)
    deadline = time.monotonic() + limit_from_env("SANDBOX_CAPTURE_JOB_SECONDS", JOB_SECONDS)
    state = {"state": "running", "phase": None, "accepted_at": accepted,
             "started_at": time.time(), "ended_at": None, "reason": None, "detail": None}
    plain = dict(os.environ)
    plain.pop("MYSQL_PWD", None)
    secret = dict(plain, MYSQL_PWD=password.decode("utf-8"))

    def phase(name):
        remaining(deadline, step)
        state["phase"] = name
        write_state(directory, state)

    try:
        remove_path(work)
        os.mkdir(work, 0o700)
        phase("preflight")
        database_bytes = int(query(mariadb(SIZE_QUERY), secret, deadline, step,
                                   "preflight_failed").split()[0])
        files_bytes = int(query(docker("exec", WP_CONTAINER, "du", "-sb", WP_ROOT), plain,
                                deadline, step, "preflight_failed").split()[0])
        need = int(2 * (database_bytes + files_bytes) * 1.10)
        info = os.statvfs(root)
        available = info.f_bavail * info.f_frsize
        if need > available:
            raise Failure("insufficient_space", {"need_bytes": need, "available_bytes": available,
                                                 "shortfall_bytes": need - available})
        phase("inventory")
        try:
            inventory = parse_inventory(query(mariadb(INVENTORY_QUERY), secret, deadline, step,
                                              "inventory_failed"))
        except ValueError:
            raise Failure("inventory_failed")
        phase("dump")
        database = os.path.join(work, "database.sql")
        run_to_file(docker("exec", "-e", "MYSQL_PWD", DB_CONTAINER, "mariadb-dump",
                           "--single-transaction", "--quick", "--routines", "--events",
                           "--triggers", "-u", DB_USER, DB_NAME),
                    database, secret, deadline, step, "dump_failed")
        phase("files")
        wordpress = os.path.join(work, "wordpress.tar")
        tar_argv = docker("exec", WP_CONTAINER, "tar", "-cf", "-", "-C", WP_ROOT,
                          "--numeric-owner", ".")
        run_to_file(tar_argv, wordpress, plain, deadline, step, "files_failed")
        phase("verify")
        wordpress_sha, wordpress_size = hash_file(wordpress)
        if hash_stream(tar_argv, plain, deadline, step, "files_failed") != (wordpress_sha, wordpress_size):
            raise Failure("source_changed")
        expected = set(item["name"] for item in inventory["tables"])
        found = dump_names(database)
        if expected != found:
            raise Failure("inventory_mismatch", {
                "missing_from_dump": sorted(expected - found)[:MAX_NAMES],
                "not_in_inventory": sorted(found - expected)[:MAX_NAMES]})
        database_sha, database_size = hash_file(database)
        phase("archive")
        pending = os.path.join(work, "archive.pending")
        try:
            os.close(create_private(pending))
            with tarfile.open(pending, "w") as archive:
                archive.add(database, arcname="database.sql", recursive=False)
                archive.add(wordpress, arcname="wordpress.tar", recursive=False)
            fd = os.open(pending, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, tarfile.TarError):
            raise Failure("archive_failed")
        remaining(deadline, step)
        archive_path = os.path.join(directory, "archive.tar")
        os.rename(pending, archive_path)
        fsync_dir(directory)
        archive_sha, archive_size = hash_file(archive_path)
        remove_path(work)
        phase("receipt")
        write_private(directory, "receipt.json", canonical({
            "schema_version": SCHEMA, "request_id": request["request_id"],
            "backup_operation_id": request["backup_operation_id"],
            "source_binding": request.get("source_binding"),
            "declarations_sha256": request["declarations_sha256"],
            "archive_sha256": archive_sha, "archive_size": archive_size,
            "members": [
                {"name": "database.sql", "sha256": database_sha, "size": database_size},
                {"name": "wordpress.tar", "sha256": wordpress_sha, "size": wordpress_size}],
            "inventory": inventory, "started_at": state["started_at"],
            "completed_at": time.time()}))
        state.update(state="complete", ended_at=time.time())
        write_state(directory, state)
    except BaseException as exc:
        failure = exc if isinstance(exc, Failure) else Failure("capture_failed")
        remove_path(work)
        for name in ("archive.tar", "receipt.json"):
            remove_path(os.path.join(directory, name))
        state.update(state="failed", reason=failure.code, detail=failure.detail,
                     ended_at=time.time())
        try:
            write_state(directory, state)
        except OSError:
            pass


OPS = {
    "start": (op_start, 2), "status": (op_status, 1), "list": (op_list, 0),
    "read-receipt": (op_read_receipt, 1), "read-declaration": (op_read_declaration, 1),
    "read-chunk": (op_read_chunk, 3), "mark-promoted": (op_mark_promoted, 2),
    "check-integrity": (op_check_integrity, 1), "retire": (op_retire, 2),
}


def main(argv):
    if len(argv) < 2 or argv[0] not in OPS:
        emit({"ok": False, "code": "request_invalid"})
        return 0
    handler, arity = OPS[argv[0]]
    root, args = argv[1], argv[2:]
    if not root.startswith("/") or len(args) != arity:
        emit({"ok": False, "code": "request_invalid"})
        return 0
    try:
        result = handler(root, *args)
    except Refusal as refusal:
        result = dict(refusal.data, ok=False, code=refusal.code)
    except Exception:
        result = {"ok": False, "code": "helper_failed"}
    if result is not None:
        emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
