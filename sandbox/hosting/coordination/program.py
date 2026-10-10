"""The remote hosting coordination program (spec 060 R1..R7).

One fixed program, sent over the authenticated SSH transport like the 061 pin
program, owns ``<remote SANDBOX_HOME>/runtime/hosting-leases``:

- ``capability.json``: written only by ``enable`` (``remote service migrate``);
  every other action refuses ``lease_authority_unavailable`` without it.
- ``coord.lock``: the flock every action runs under, so admission is
  linearizable across controllers.
- ``targets/<sha16(state_key)>.json``: lease, hold, phase, fence, queue, history.
- ``remote.json``: build cap and slots, the remote-wide lease, controllers.

Inputs are the remote Sandbox home and one base64 JSON request; output is one
JSON line. Refusals are ``{"ok": true, "refused": <code>, ...}`` so the client
can tell a decision from a failure. A malformed request exits non-zero. The
program never clears a 054/062 recovery fence: ``fenced_pending_cessation``
only records that an expired holder's phase may still be running.
"""
from __future__ import annotations

import base64
import json
import shlex

LEASE_TTL_SECONDS = 90
SHARED_MAX_SECONDS = 60
HOLD_DEFAULT_SECONDS = 3600
HOLD_MAX_SECONDS = 4 * 3600
WAITER_LAPSE_SECONDS = 10
DEFAULT_BUILD_CAP = 2
MAX_BUILD_CAP = 64
MAX_TARGETS_LISTED = 64
MAX_QUEUE = 32
MAX_HISTORY = 64
MAX_PURPOSE = 120
MAX_REASON = 200

ACTIONS = (
    "admit", "renew", "release", "phase-report", "hold-claim", "hold-renew",
    "hold-release", "hold-break", "build-acquire", "build-release",
    "shared-acquire", "shared-release", "cap-get", "cap-set", "list",
    "register-controller", "capability", "enable",
)

PROGRAM = r'''
import base64, fcntl, hashlib, json, os, re, sys, time
home = sys.argv[1]
request = json.loads(base64.b64decode(sys.argv[2]).decode())
if not isinstance(request, dict):
    raise SystemExit(3)
TTL, SHARED_MAX, HOLD_DEFAULT, HOLD_MAX = 90, 60, 3600, 14400
WAITER_LAPSE, CAP_DEFAULT, CAP_MAX = 10, 2, 64
MAX_TARGETS, MAX_QUEUE, MAX_HISTORY, MAX_CONTROLLERS = 64, 32, 64, 256
CONTROLLER = re.compile(r"h-[0-9a-f]{16}")
LEASE = re.compile(r"l-[0-9a-f]{16}")
HOLD = re.compile(r"hd-[0-9a-f]{16}")
WAITER = re.compile(r"w-[0-9a-f]{16}")
OPERATION = re.compile(r"[a-z][a-z0-9-]{0,39}")
SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
STEP = re.compile(r"[a-z][a-z0-9-]{0,39}")
root = os.path.join(home, "runtime", "hosting-leases")
marker = os.path.join(root, "capability.json")
targets_dir = os.path.join(root, "targets")
remote_path = os.path.join(root, "remote.json")
now = int(time.time())
action = request.get("action")
READS = ("list", "cap-get", "capability")

def out(value):
    value.setdefault("ok", True)
    value["action"] = action
    value["now"] = now
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(0)

def bad():
    raise SystemExit(3)

def text(value, limit):
    if not isinstance(value, str) or not 0 < len(value) <= limit \
            or not all(ch.isprintable() for ch in value):
        bad()
    return value

def match(pattern, value):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        bad()
    return value

def bounded(value, low, high, default):
    if value is None:
        return default
    if type(value) is not int or not low <= value <= high:
        bad()
    return value

def state_key(value):
    if not isinstance(value, str):
        bad()
    parts = value.split("/")
    if len(parts) != 3 or not all(SEGMENT.fullmatch(p) for p in parts):
        bad()
    return value

def holder(value, operation=True):
    if not isinstance(value, dict):
        bad()
    clean = {"controller_id": match(CONTROLLER, value.get("controller_id")),
             "session": text(value.get("session"), 64)}
    if operation:
        clean["operation"] = match(OPERATION, value.get("operation"))
        clean["request_id"] = text(value.get("request_id"), 64)
    return clean

def new_id(prefix):
    return prefix + os.urandom(8).hex()

if action == "capability":
    out({"enabled": os.path.isfile(marker)})
if action not in ("admit", "renew", "release", "phase-report", "hold-claim", "hold-renew",
                  "hold-release", "hold-break", "build-acquire", "build-release",
                  "shared-acquire", "shared-release", "cap-get", "cap-set", "list",
                  "register-controller", "enable"):
    raise SystemExit(2)
if action == "enable":
    os.makedirs(root, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
if not os.path.isfile(marker) and action != "enable":
    # Read-only and refusing: never create the store for a runtime that has
    # not been migrated to this feature.
    out({"refused": "lease_authority_unavailable", "reason": "capability_missing"})
lock = os.open(os.path.join(root, "coord.lock"),
               os.O_RDONLY if action in READS else os.O_CREAT | os.O_RDWR, 0o600) \
    if action not in READS or os.path.exists(os.path.join(root, "coord.lock")) else None
if lock is not None:
    fcntl.flock(lock, fcntl.LOCK_SH if action in READS else fcntl.LOCK_EX)

def put(target, value):
    temp = target + ".tmp"
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, sort_keys=True)
    os.replace(temp, target)

def load(target):
    try:
        with open(target) as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return False
    return value if isinstance(value, dict) else False

if action == "enable":
    put(marker, {"capability": "hosting_coordination", "version": 1, "enabled_at": now})
    out({"enabled": True})

def target_file(key):
    return os.path.join(targets_dir, hashlib.sha256(key.encode()).hexdigest()[:16] + ".json")

def history(record, entry):
    entry["at"] = now
    record["history"] = (record.get("history") or [])[-(MAX_HISTORY - 1):] + [entry]

def alive(phase):
    if phase.get("ended"):
        return False
    pid = phase.get("pid")
    if type(pid) is not int or pid <= 1:
        return True  # no probe: cessation cannot be shown, so assume running
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    start = phase.get("start")
    if isinstance(start, str):
        try:
            with open("/proc/%d/stat" % pid) as handle:
                current = handle.read().rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            current = None
        if current is not None and current != start:
            return False
    return True

def normalize(record):
    queue = [w for w in record.get("queue") or [] if w.get("deadline", 0) > now]
    record["queue"] = queue
    lease = record.get("lease")
    if lease and lease["expires_at"] <= now:
        record["lease"] = None
        phase = record.get("phase")
        if phase and phase.get("lease_id") == lease["lease_id"] and alive(phase):
            record["fence"] = "fenced_pending_cessation"
            history(record, {"event": "expired_with_phase", "lease_id": lease["lease_id"],
                             "holder": lease["holder"], "phase_id": phase["phase_id"]})
        else:
            record["phase"] = None
            history(record, {"event": "expired", "lease_id": lease["lease_id"],
                             "holder": lease["holder"]})
    hold = record.get("hold")
    if hold and hold["expires_at"] <= now:
        record["hold"] = None
        history(record, {"event": "hold_expired", "hold_id": hold["hold_id"]})
    if record.get("fence") == "fenced_pending_cessation":
        phase = record.get("phase")
        if not phase or not alive(phase):
            record["fence"] = "none"
            record["phase"] = None
            history(record, {"event": "phase_ceased",
                             "phase_id": phase.get("phase_id") if phase else None})
    return record

def read_target(key, create):
    value = load(target_file(key))
    if value is False or (value is not None and value.get("state_key") != key):
        out({"refused": "lease_authority_unavailable", "reason": "target_record_unreadable",
             "target": key})
    if value is None:
        if not create:
            return None
        value = {"state_key": key, "fencing_token": 0, "lease": None, "hold": None,
                 "phase": None, "fence": "none", "queue": [], "history": []}
    return normalize(value)

def write_target(record):
    os.makedirs(targets_dir, mode=0o700, exist_ok=True)
    os.chmod(targets_dir, 0o700)
    put(target_file(record["state_key"]), record)

def read_remote(create):
    value = load(remote_path)
    if value is False:
        out({"refused": "lease_authority_unavailable", "reason": "remote_record_unreadable"})
    if value is None:
        value = {"build_cap": CAP_DEFAULT, "cap_history": [], "build_slots": [],
                 "shared_lease": None, "controllers": []}
    shared = value.get("shared_lease")
    if shared and shared["expires_at"] <= now:
        value["shared_lease"] = None
    live = []
    for slot in value.get("build_slots") or []:
        if slot["expires_at"] <= now:
            continue
        record = read_target(slot["state_key"], False)
        lease = record.get("lease") if record else None
        if lease and lease["lease_id"] == slot["lease_id"]:
            slot["expires_at"] = lease["expires_at"]
            live.append(slot)
    value["build_slots"] = live
    return value

def write_remote(value):
    put(remote_path, value)

def hold_view(hold):
    view = {k: hold[k] for k in ("hold_id", "purpose", "claimed_at", "expires_at")}
    view["controller"] = hold["holder"]["controller_id"]
    return view

def owned_lease(record):
    lease = record.get("lease")
    return lease if lease and lease["lease_id"] == request.get("lease_id") \
        and record.get("fencing_token") == request.get("fencing_token") else None

if action == "list":
    remote = read_remote(False)
    targets = []
    names = sorted(os.listdir(targets_dir)) if os.path.isdir(targets_dir) else []
    names = [n for n in names if re.fullmatch(r"[0-9a-f]{16}\.json", n)]
    for name in names[:MAX_TARGETS]:
        value = load(os.path.join(targets_dir, name))
        if not value:
            targets.append({"target": None, "state": "unreadable", "file": name})
            continue
        value = normalize(value)
        lease, hold = value.get("lease"), value.get("hold")
        state = "fenced" if value.get("fence") == "fenced_pending_cessation" else \
            "leased" if lease else "held" if hold else "idle"
        entry = {"target": value["state_key"], "state": state,
                 "fencing_token": value.get("fencing_token", 0),
                 "queue": [{"operation": w["holder"]["operation"],
                            "controller": w["holder"]["controller_id"],
                            "enqueued_at": w["enqueued_at"]}
                           for w in value.get("queue", [])[:MAX_QUEUE]]}
        if lease:
            entry.update(operation=lease["holder"]["operation"], holder=lease["holder"],
                         since=lease["started_at"])
        elif value.get("phase"):
            entry.update(holder=value["phase"].get("holder"),
                         since=value["phase"].get("dispatched_at"))
        if hold:
            entry["hold"] = hold_view(hold)
        targets.append(entry)
    shared = remote.get("shared_lease")
    out({"build_cap": remote["build_cap"],
         "build_holders": [{"target": s["state_key"], "lease_id": s["lease_id"],
                            "since": s["started_at"]} for s in remote["build_slots"]],
         "shared_lease": {"step": shared["step"], "since": shared["acquired_at"],
                          "target": shared["state_key"]} if shared else None,
         "targets": targets, "truncated": len(names) > MAX_TARGETS})

if action == "cap-get":
    remote = read_remote(False)
    out({"build_cap": remote["build_cap"], "cap_history": remote.get("cap_history", [])[-16:],
         "build_holders": [{"target": s["state_key"], "lease_id": s["lease_id"],
                            "since": s["started_at"]} for s in remote["build_slots"]]})

if action == "cap-set":
    value = bounded(request.get("value"), 1, CAP_MAX, None)
    if value is None:
        bad()
    controller = match(CONTROLLER, request.get("controller_id"))
    remote = read_remote(True)
    remote["build_cap"] = value
    remote["cap_history"] = (remote.get("cap_history") or [])[-63:] + [
        {"value": value, "controller_id": controller, "at": now}]
    write_remote(remote)
    out({"build_cap": value})

if action == "register-controller":
    controller = match(CONTROLLER, request.get("controller_id"))
    remote = read_remote(True)
    controllers = remote.get("controllers") or []
    if controller not in controllers:
        if len(controllers) >= MAX_CONTROLLERS:
            out({"refused": "controller_capacity_reached"})
        controllers.append(controller)
    remote["controllers"] = controllers
    write_remote(remote)
    out({"registered": True})

if action in ("shared-acquire", "shared-release"):
    remote = read_remote(True)
    shared = remote.get("shared_lease")
    if action == "shared-release":
        lease_id = match(LEASE, request.get("lease_id"))
        released = bool(shared and shared["lease_id"] == lease_id)
        if released:
            remote["shared_lease"] = None
            write_remote(remote)
        out({"released": released})
    key = state_key(request.get("state_key"))
    step = match(STEP, request.get("step"))
    bound = bounded(request.get("bound"), 1, SHARED_MAX, SHARED_MAX)
    if shared:
        out({"refused": "shared_lease_busy", "shared_lease": {
            "step": shared["step"], "target": shared["state_key"],
            "since": shared["acquired_at"], "expires_at": shared["expires_at"]}})
    lease = {"lease_id": new_id("l-"), "state_key": key, "step": step,
             "acquired_at": now, "expires_at": now + bound}
    remote["shared_lease"] = lease
    write_remote(remote)
    out({"shared_lease": lease})

key = state_key(request.get("state_key"))

if action in ("build-acquire", "build-release"):
    lease_id = match(LEASE, request.get("lease_id"))
    remote = read_remote(True)
    slots = remote["build_slots"]
    if action == "build-release":
        kept = [s for s in slots if s["lease_id"] != lease_id]
        remote["build_slots"] = kept
        write_remote(remote)
        out({"released": len(kept) != len(slots)})
    record = read_target(key, False)
    lease = record.get("lease") if record else None
    if not lease or lease["lease_id"] != lease_id:
        out({"refused": "lease_lost", "target": key})
    if any(s["lease_id"] == lease_id for s in slots):
        out({"acquired": True, "build_cap": remote["build_cap"]})
    if len(slots) >= remote["build_cap"]:
        out({"refused": "build_cap_reached", "target": key, "cap": remote["build_cap"],
             "build_holders": [{"target": s["state_key"], "lease_id": s["lease_id"],
                                "since": s["started_at"]} for s in slots]})
    slots.append({"lease_id": lease_id, "state_key": key, "started_at": now,
                  "expires_at": lease["expires_at"]})
    write_remote(remote)
    out({"acquired": True, "build_cap": remote["build_cap"]})

persist = os.path.exists(target_file(key))
record = read_target(key, True)

def finish(value):
    write_target(record)
    out(value)

if action == "admit":
    who = holder(request.get("holder"))
    ttl = bounded(request.get("ttl"), 1, TTL, TTL)
    hold_id = request.get("hold_id")
    if hold_id is not None:
        match(HOLD, hold_id)
    waiter = request.get("waiter")
    if waiter is not None:
        if not isinstance(waiter, dict):
            bad()
        waiter_id = match(WAITER, waiter.get("waiter_id"))
        deadline = bounded(waiter.get("deadline"), now, now + 3600 + 60, None)
        if deadline is None:
            bad()
    lease, hold, phase = record.get("lease"), record.get("hold"), record.get("phase")
    refusal = None
    if record.get("fence") == "fenced_pending_cessation":
        refusal = {"refused": "predecessor_phase_running",
                   "holder": phase.get("holder") if phase else None,
                   "since": phase.get("dispatched_at") if phase else None}
    elif lease:
        refusal = {"refused": "target_busy", "holder": lease["holder"],
                   "since": lease["started_at"]}
    elif hold and hold["hold_id"] != hold_id:
        refusal = {"refused": "target_held", "hold": hold_view(hold)}
    elif hold is None and hold_id is not None:
        refusal = {"refused": "hold_not_owned"}
    elif not hold:
        ahead = [w for w in record["queue"]
                 if waiter is None or w["waiter_id"] != waiter_id]
        mine = waiter is not None and any(w["waiter_id"] == waiter_id for w in record["queue"])
        if mine:
            ahead = ahead[:[w["waiter_id"] for w in record["queue"]].index(waiter_id)]
        if ahead:
            head = ahead[0]
            refusal = {"refused": "target_busy", "holder": head["holder"],
                       "since": head["enqueued_at"], "queued": True}
    if refusal is not None:
        refusal["target"] = key
        if waiter is not None and refusal["refused"] in ("target_busy", "target_held",
                                                         "predecessor_phase_running"):
            ids = [w["waiter_id"] for w in record["queue"]]
            entry_deadline = min(deadline, now + WAITER_LAPSE)
            if waiter_id in ids:
                record["queue"][ids.index(waiter_id)]["deadline"] = entry_deadline
            elif len(ids) >= MAX_QUEUE:
                refusal["queue_full"] = True
            else:
                record["queue"].append({"waiter_id": waiter_id, "holder": who,
                                        "enqueued_at": now, "deadline": entry_deadline})
            ids = [w["waiter_id"] for w in record["queue"]]
            refusal["position"] = ids.index(waiter_id) + 1 if waiter_id in ids else None
            finish(refusal)
        if persist:
            finish(refusal)
        out(refusal)
    record["fencing_token"] = int(record.get("fencing_token", 0)) + 1
    new = {"lease_id": new_id("l-"), "kind": "hold-nested" if hold else "operation",
           "holder": who, "started_at": now, "renewed_at": now, "expires_at": now + ttl}
    record["lease"] = new
    record["phase"] = None
    if waiter is not None:
        record["queue"] = [w for w in record["queue"] if w["waiter_id"] != waiter_id]
    history(record, {"event": "admit", "lease_id": new["lease_id"], "holder": who,
                     "fencing_token": record["fencing_token"]})
    finish({"admitted": True, "lease": new, "fencing_token": record["fencing_token"],
            "target": key})

if action == "renew":
    match(LEASE, request.get("lease_id"))
    ttl = bounded(request.get("ttl"), 1, TTL, TTL)
    lease = owned_lease(record)
    if lease is None:
        out({"lost": True, "target": key})
    lease["renewed_at"] = now
    lease["expires_at"] = now + ttl
    finish({"lost": False, "lease": lease})

if action == "release":
    match(LEASE, request.get("lease_id"))
    lease = owned_lease(record)
    if lease is None:
        out({"released": False, "target": key})
    record["lease"] = None
    record["phase"] = None
    history(record, {"event": "release", "lease_id": lease["lease_id"]})
    remote = read_remote(True)
    remote["build_slots"] = [s for s in remote["build_slots"]
                             if s["lease_id"] != lease["lease_id"]]
    write_remote(remote)
    finish({"released": True, "target": key})

if action == "phase-report":
    match(LEASE, request.get("lease_id"))
    event = request.get("event")
    phase_id = text(request.get("phase_id"), 64)
    phase = record.get("phase")
    if event == "end":
        # Accepted from a holder whose lease expired: an ended phase is the
        # evidence that lets a fenced target leave fenced_pending_cessation.
        if phase and phase["phase_id"] == phase_id \
                and phase["lease_id"] == request.get("lease_id"):
            phase["ended"] = now
            normalize(record)
            if record.get("lease") and record["lease"]["lease_id"] == phase["lease_id"]:
                record["phase"] = None
            finish({"recorded": True, "fence": record.get("fence")})
        out({"recorded": False, "fence": record.get("fence")})
    if event not in ("start", "check"):
        bad()
    lease = owned_lease(record)
    if lease is None:
        out({"refused": "lease_lost", "target": key})
    if event == "check":
        out({"valid": True, "fencing_token": record["fencing_token"]})
    pid = request.get("pid")
    if pid is not None and (type(pid) is not int or pid <= 1):
        bad()
    start = request.get("start")
    if start is not None:
        text(start, 32)
    record["phase"] = {"phase_id": phase_id, "lease_id": lease["lease_id"],
                       "dispatched_at": now, "holder": lease["holder"],
                       "pid": pid, "start": start}
    finish({"recorded": True, "fencing_token": record["fencing_token"]})

if action == "hold-claim":
    who = holder(request.get("holder"), operation=False)
    purpose = text(request.get("purpose"), 120)
    duration = request.get("duration", HOLD_DEFAULT)
    if type(duration) is not int or duration < 1:
        bad()
    if duration > HOLD_MAX:
        out({"refused": "hold_too_long", "target": key, "maximum": HOLD_MAX})
    if record.get("hold"):
        out({"refused": "target_held", "target": key, "hold": hold_view(record["hold"])})
    if record.get("fence") == "fenced_pending_cessation" or record.get("lease"):
        lease = record.get("lease")
        out({"refused": "target_busy" if lease else "predecessor_phase_running",
             "target": key, "holder": lease["holder"] if lease else
             (record.get("phase") or {}).get("holder")})
    hold = {"hold_id": new_id("hd-"), "holder": who, "purpose": purpose,
            "claimed_at": now, "renewed_at": now, "expires_at": now + duration}
    record["hold"] = hold
    history(record, {"event": "hold_claimed", "hold_id": hold["hold_id"],
                     "controller_id": who["controller_id"], "purpose": purpose})
    finish({"hold": hold_view(hold), "target": key})

if action in ("hold-renew", "hold-release"):
    hold_id = match(HOLD, request.get("hold_id"))
    hold = record.get("hold")
    if not hold or hold["hold_id"] != hold_id:
        out({"refused": "hold_not_owned", "target": key})
    if action == "hold-release":
        record["hold"] = None
        history(record, {"event": "hold_released", "hold_id": hold_id})
        finish({"released": True, "target": key})
    duration = request.get("duration", HOLD_DEFAULT)
    if type(duration) is not int or duration < 1:
        bad()
    if now + duration > hold["claimed_at"] + HOLD_MAX:
        out({"refused": "hold_renewal_exceeds_maximum", "target": key,
             "latest_expiry": hold["claimed_at"] + HOLD_MAX})
    hold["renewed_at"] = now
    hold["expires_at"] = now + duration
    finish({"hold": hold_view(hold), "target": key})

if action == "hold-break":
    controller = match(CONTROLLER, request.get("controller_id"))
    reason = text(request.get("reason"), 200)
    hold = record.get("hold")
    if not hold:
        out({"broken": False, "target": key})
    record["hold"] = None
    history(record, {"event": "broken_hold", "hold_id": hold["hold_id"],
                     "breaker_controller": controller, "reason": reason})
    finish({"broken": True, "hold": hold_view(hold), "target": key})

raise SystemExit(2)
'''


def remote_command(request: dict) -> str:
    """The shell command running ``PROGRAM`` for ``request`` on the remote."""
    payload = base64.b64encode(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).decode()
    return (f"python3 -c {shlex.quote(PROGRAM)} "
            f"\"${{SANDBOX_HOME:-$HOME/sandbox}}\" {shlex.quote(payload)}")
