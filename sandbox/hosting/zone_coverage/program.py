"""The remote zone-coverage store program (spec 070 contracts/zone-store.md).

One fixed program, sent over the authenticated SSH transport like coordination,
owns ``<remote SANDBOX_HOME>/runtime/zone-coverage/``:

- ``coverage.lock``: flock held for every action (10 s wait bound).
- ``zones/<zone>.json``: 0600, written via temp file, fsynced, renamed, parent fsynced.
- Corrupt document refuses and is never rewritten.
- Max request 64 KiB, max doc 256 KiB, max 512 dependents, max 64 history.

Inputs: remote Sandbox home (sys.argv[1]) and one base64 JSON request (sys.argv[2]).
Output: one JSON line. Refusals are ``{"ok": true, "refused": <code>, ...}``.
"""
from __future__ import annotations

import base64
import json
import shlex

ACTIONS = (
    "read",
    "list",
    "begin",
    "phase",
    "finish",
    "dependent-bind",
    "dependent-release",
)

PROGRAM = r'''
import base64, fcntl, hashlib, json, os, re, secrets, sys, time

home = sys.argv[1]
raw_arg = sys.argv[2]
if len(raw_arg) > 90000:
    raise SystemExit(3)

try:
    decoded = base64.b64decode(raw_arg).decode("utf-8")
    if len(decoded) > 65536:
        raise SystemExit(3)
    request = json.loads(decoded)
except Exception:
    raise SystemExit(3)

if not isinstance(request, dict):
    raise SystemExit(3)

action = request.get("action")
if action not in ("read", "list", "begin", "phase", "finish", "dependent-bind", "dependent-release"):
    raise SystemExit(3)

now = int(time.time())
LEASE_SECONDS = 900
LOCK_WAIT_SECONDS = 10
MAX_DOC_BYTES = 256 * 1024
MAX_DEPENDENTS = 512
MAX_HISTORY = 64

SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ZONE_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

def out(val):
    val.setdefault("ok", True)
    val["action"] = action
    val["now"] = now
    print(json.dumps(val, sort_keys=True))
    raise SystemExit(0)

def bad():
    raise SystemExit(3)

def check_zone(zone):
    if not isinstance(zone, str):
        bad()
    clean = zone.strip().rstrip(".").lower()
    try:
        encoded = clean.encode("idna").decode("ascii")
    except Exception:
        bad()
    labels = encoded.split(".")
    if len(labels) < 2 or not all(ZONE_LABEL_RE.match(lbl) for lbl in labels):
        bad()
    return encoded

def check_state_key(key):
    if not isinstance(key, str):
        bad()
    parts = key.split("/")
    if len(parts) != 3 or not all(SEGMENT_RE.match(p) for p in parts):
        bad()
    return key

# Acquire lock
root_dir = os.path.join(home, "runtime", "zone-coverage")
zones_dir = os.path.join(root_dir, "zones")
os.makedirs(zones_dir, exist_ok=True)
lock_path = os.path.join(root_dir, "coverage.lock")
lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)

deadline = time.time() + LOCK_WAIT_SECONDS
acquired = False
while time.time() < deadline:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        break
    except (BlockingIOError, OSError):
        time.sleep(0.05)

if not acquired:
    out({"ok": True, "refused": "coverage_store_unavailable", "reason": "lock_wait_timeout"})

def doc_path_for(zone):
    return os.path.join(zones_dir, f"{zone}.json")

def load_doc(zone):
    p = doc_path_for(zone)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "rb") as f:
            content = f.read()
        val = json.loads(content.decode("utf-8"))
        if not isinstance(val, dict) or val.get("schema_version") is None:
            raise ValueError("corrupt document structure")
        return val
    except Exception as exc:
        out({"ok": True, "refused": "coverage_document_corrupt", "reason": str(exc)})

def save_doc(zone, doc):
    p = doc_path_for(zone)
    raw = json.dumps(doc, indent=2, sort_keys=True).encode("utf-8")
    if len(raw) > MAX_DOC_BYTES:
        out({"ok": True, "refused": "coverage_store_unavailable", "reason": "document_size_exceeded"})
    tmp_path = f"{p}.tmp.{os.getpid()}_{secrets.token_hex(4)}"
    fd = os.open(tmp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_path, p)
    try:
        dfd = os.open(zones_dir, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass

# Actions
if action == "read":
    zone = check_zone(request.get("zone"))
    doc = load_doc(zone)
    if doc is None:
        out({"state": "absent", "document": None})
    out({"state": doc.get("state"), "document": doc})

elif action == "list":
    rows = []
    if os.path.isdir(zones_dir):
        for fname in sorted(os.listdir(zones_dir)):
            if fname.endswith(".json"):
                zname = fname[:-5]
                p = os.path.join(zones_dir, fname)
                try:
                    with open(p, "rb") as f:
                        data = json.loads(f.read().decode("utf-8"))
                    rows.append({"zone": zname, "state": data.get("state")})
                except Exception:
                    rows.append({"zone": zname, "state": "corrupt"})
    out({"zones": rows})

elif action == "begin":
    zone = check_zone(request.get("zone"))
    kind = request.get("kind")
    if kind not in ("enable", "disable", "repair"):
        bad()
    controller = request.get("controller") or "unknown"
    exp_gen = request.get("expected_generation")
    exp_dep_rev = request.get("expected_dependents_revision")
    resume = bool(request.get("resume"))

    doc = load_doc(zone)
    if doc is None:
        if exp_gen is not None and exp_gen != 0:
            out({"ok": True, "refused": "coverage_generation_stale", "generation": 0, "expected_generation": exp_gen})
        if exp_dep_rev is not None and exp_dep_rev != 0:
            out({"ok": True, "refused": "coverage_dependents_stale", "dependents_revision": 0, "expected_dependents_revision": exp_dep_rev})
        new_op_id = f"op-{secrets.token_hex(8)}"
        doc = {
            "schema_version": 1,
            "zone": zone,
            "zone_id": request.get("zone_id") or "",
            "state": "enabling",
            "generation": 0,
            "operation": {
                "id": new_op_id,
                "kind": kind,
                "phase": "begin",
                "controller": controller,
                "started_at": now,
                "lease_expires_at": now + LEASE_SECONDS,
            },
            "interrupted_kind": None,
            "shared_records": [],
            "fallback": None,
            "exceptions": [],
            "dependents": {},
            "dependents_revision": 0,
            "history": [],
        }
        save_doc(zone, doc)
        out({"document": doc, "operation_id": new_op_id})

    # Doc exists
    cur_gen = doc.get("generation", 0)
    if exp_gen is not None and exp_gen != cur_gen:
        out({"ok": True, "refused": "coverage_generation_stale", "generation": cur_gen, "expected_generation": exp_gen})

    cur_dep_rev = doc.get("dependents_revision", 0)
    if exp_dep_rev is not None and exp_dep_rev != cur_dep_rev:
        out({"ok": True, "refused": "coverage_dependents_stale", "dependents_revision": cur_dep_rev, "expected_dependents_revision": exp_dep_rev})

    existing_op = doc.get("operation")
    if existing_op:
        lease_exp = existing_op.get("lease_expires_at", 0)
        if now < lease_exp:
            out({"ok": True, "refused": "coverage_operation_active", "operation": existing_op})
        # Lease is expired
        if not resume:
            out({"ok": True, "refused": "coverage_operation_active", "operation": existing_op})
        # Resuming expired lease
        if existing_op.get("kind") != kind:
            out({"ok": True, "refused": "coverage_kind_mismatch", "operation": existing_op, "requested_kind": kind})

    new_op_id = f"op-{secrets.token_hex(8)}"
    if kind == "enable":
        new_state = "enabling"
    elif kind == "disable":
        new_state = "disabling"
    else:
        new_state = doc.get("state", "active")

    doc["state"] = new_state
    doc["operation"] = {
        "id": new_op_id,
        "kind": kind,
        "phase": "begin",
        "controller": controller,
        "started_at": now,
        "lease_expires_at": now + LEASE_SECONDS,
    }
    save_doc(zone, doc)
    out({"document": doc, "operation_id": new_op_id})

elif action == "phase":
    zone = check_zone(request.get("zone"))
    op_id = request.get("operation_id")
    phase_name = request.get("phase")
    updates = request.get("updates") or {}

    doc = load_doc(zone)
    if doc is None:
        out({"ok": True, "refused": "coverage_not_active"})

    cur_op = doc.get("operation")
    if not cur_op or cur_op.get("id") != op_id:
        out({"ok": True, "refused": "coverage_operation_active", "reason": "not_holder"})

    cur_op["lease_expires_at"] = now + LEASE_SECONDS
    cur_op["phase"] = phase_name
    for k in ("fallback", "shared_records", "exceptions", "zone_id"):
        if k in updates:
            doc[k] = updates[k]

    save_doc(zone, doc)
    out({"document": doc, "lease_expires_at": cur_op["lease_expires_at"]})

elif action == "finish":
    zone = check_zone(request.get("zone"))
    op_id = request.get("operation_id")
    final_state = request.get("state")
    interrupted_kind = request.get("interrupted_kind")
    raise_gen = bool(request.get("raise_generation"))

    doc = load_doc(zone)
    if doc is None:
        out({"ok": True, "refused": "coverage_not_active"})

    cur_op = doc.get("operation")
    if not cur_op or cur_op.get("id") != op_id:
        out({"ok": True, "refused": "coverage_operation_active", "reason": "not_holder"})

    doc["state"] = final_state
    if final_state == "incomplete":
        doc["interrupted_kind"] = interrupted_kind or cur_op.get("kind")
    else:
        doc["interrupted_kind"] = None

    if raise_gen:
        doc["generation"] = doc.get("generation", 0) + 1

    history = doc.setdefault("history", [])
    history.append({
        "at": now,
        "kind": cur_op.get("kind"),
        "result": final_state,
        "operation_id": op_id,
    })
    doc["history"] = history[-MAX_HISTORY:]
    doc["operation"] = None

    save_doc(zone, doc)
    out({"document": doc})

elif action == "dependent-bind":
    zone = check_zone(request.get("zone"))
    state_key = check_state_key(request.get("state_key"))
    hostname = request.get("hostname")
    dep_state = request.get("state")
    if dep_state not in ("pending", "bound", "recovered"):
        bad()

    doc = load_doc(zone)
    if doc is None:
        out({"ok": True, "refused": "coverage_not_active"})

    if dep_state == "pending":
        if doc.get("state") != "active":
            out({"ok": True, "refused": "coverage_not_active"})
        op = doc.get("operation")
        if op and op.get("kind") == "disable" and now < op.get("lease_expires_at", 0):
            out({"ok": True, "refused": "coverage_operation_active"})

    dependents = doc.setdefault("dependents", {})
    if state_key not in dependents and len(dependents) >= MAX_DEPENDENTS:
        out({"ok": True, "refused": "coverage_store_unavailable", "reason": "max_dependents_exceeded"})

    dependents[state_key] = {
        "hostname": hostname,
        "state": dep_state,
        "operation_id": request.get("operation_id"),
        "generation": request.get("generation", doc.get("generation", 0)),
        "updated_at": now,
    }
    doc["dependents_revision"] = doc.get("dependents_revision", 0) + 1
    save_doc(zone, doc)
    out({"document": doc, "dependents_revision": doc["dependents_revision"]})

elif action == "dependent-release":
    zone = check_zone(request.get("zone"))
    state_key = check_state_key(request.get("state_key"))

    doc = load_doc(zone)
    if doc is not None:
        dependents = doc.setdefault("dependents", {})
        if state_key in dependents:
            del dependents[state_key]
            doc["dependents_revision"] = doc.get("dependents_revision", 0) + 1
            save_doc(zone, doc)
    rev = (doc or {}).get("dependents_revision", 0)
    out({"document": doc, "dependents_revision": rev})
'''


def remote_command(request: dict, *, home: str = "$HOME/.sandbox") -> str:
    """Format the shell command that invokes PROGRAM on the remote."""
    payload = base64.b64encode(json.dumps(request, separators=(",", ":")).encode()).decode()
    return f"python3 -c {shlex.quote(PROGRAM)} {shlex.quote(home)} {payload}"
