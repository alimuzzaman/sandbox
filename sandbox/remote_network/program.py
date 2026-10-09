"""The fixed remote range program (spec 063 research R2, contract).

It is :mod:`sandbox.remote_network.ranges` verbatim (stdlib only) followed by a
driver, sent over the authenticated SSH transport like the 061 pin program.
One source of truth decides overlap on both sides. State lives under the
remote ``$SANDBOX_HOME/runtime/network-ranges`` (directory 0700, files 0600);
every write happens under an exclusive flock on ``state.lock``, and the
read-only operations never create the directory.
"""
from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path

MAX_RANGES = 16
MAX_LISTED = 256
MAX_TABLE = 32
MAX_NETWORKS_PER_REQUEST = 16
MAX_OBSERVED = 4096

_DRIVER = r'''

# ---- driver -----------------------------------------------------------------
import base64 as _b64, fcntl as _fcntl, json as _json, os as _os, re as _re
import subprocess as _sp, sys as _sys, time as _time

_home = _sys.argv[1]
_request = _json.loads(_b64.b64decode(_sys.argv[2]).decode())
_root = _os.path.join(_home, "runtime", "network-ranges")
_state_path = _os.path.join(_root, "state.json")
_now = int(_time.time())
_op = _request.get("op")
_ID = _re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_NET = _re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_HOLDER = _re.compile(r"h-[0-9a-f]{16}")


def _emit(value):
    value.setdefault("ok", True)
    value["op"] = _op
    print(_json.dumps(value, sort_keys=True))
    raise SystemExit(0)


def _refuse(code, message, **data):
    _emit({"ok": False, "code": code, "message": message, "data": data})


def _run(argv):
    try:
        done = _sp.run(argv, capture_output=True, text=True, timeout=8)
    except (OSError, _sp.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _loads(text):
    try:
        return _json.loads(text) if text is not None else None
    except ValueError:
        return None


def _observe():
    status, nets, routes = "complete", set(), set()
    ids = _run(["docker", "network", "ls", "-q", "--no-trunc"])
    if ids is None:
        status = "partial"
    elif ids.split():
        data = _loads(_run(["docker", "network", "inspect", *ids.split()]))
        if not isinstance(data, list):
            status = "partial"
        else:
            for item in data:
                ipam = (item or {}).get("IPAM") if isinstance(item, dict) else None
                for cfg in ((ipam or {}).get("Config") or []) if isinstance(ipam, dict) else []:
                    if isinstance(cfg, dict) and isinstance(cfg.get("Subnet"), str):
                        nets.add(cfg["Subnet"])
    data = _loads(_run(["ip", "-j", "-4", "route", "show", "table", "all"]))
    if not isinstance(data, list):
        status = "partial"
    else:
        for item in data:
            dst = item.get("dst") if isinstance(item, dict) else None
            if isinstance(dst, str) and dst != "default":
                routes.add(dst if "/" in dst else dst + "/32")
    if len(nets) > MAX_OBSERVED or len(routes) > MAX_OBSERVED:
        status = "partial"
    return {"status": status, "networks": sorted(nets)[:MAX_OBSERVED],
            "routes": sorted(routes)[:MAX_OBSERVED]}


def _lock(exclusive):
    if exclusive:
        _os.makedirs(_root, mode=0o700, exist_ok=True)
        _os.chmod(_root, 0o700)
        fd = _os.open(_os.path.join(_root, "state.lock"), _os.O_CREAT | _os.O_RDWR, 0o600)
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        return fd
    try:
        fd = _os.open(_os.path.join(_root, "state.lock"), _os.O_RDONLY)
    except FileNotFoundError:
        return None
    _fcntl.flock(fd, _fcntl.LOCK_SH)
    return fd


def _load():
    try:
        with open(_state_path) as handle:
            state = _json.load(handle)
    except FileNotFoundError:
        return {"schema": 1, "ranges": [], "allocations": [], "capacity_proof": None}
    except (OSError, ValueError):
        _refuse("range_state_invalid", "remote range state is unreadable; nothing was changed")
    if (not isinstance(state, dict) or state.get("schema") != 1
            or not isinstance(state.get("ranges"), list)
            or not isinstance(state.get("allocations"), list)):
        _refuse("range_state_invalid", "remote range state is invalid; nothing was changed")
    return state


def _save(state):
    temp = _state_path + ".tmp"
    fd = _os.open(temp, _os.O_CREAT | _os.O_WRONLY | _os.O_TRUNC, 0o600)
    with _os.fdopen(fd, "w") as handle:
        _json.dump(state, handle, sort_keys=True)
        handle.flush()
        _os.fsync(handle.fileno())
    _os.chmod(temp, 0o600)
    _os.replace(temp, _state_path)


def _ranges(state):
    return [parse_range(r["cidr"], r["subnet_prefix"]) for r in state["ranges"]]


def _range_stats(state):
    capacity = sum(r.capacity for r in _ranges(state))
    allocated = len(state["allocations"])
    return {"capacity": capacity, "allocated": allocated, "usable": max(0, capacity - allocated)}


def _table(allocations):
    rows = sorted(allocations, key=lambda a: (a["allocated_at"], a["allocation_id"]))
    return [{"allocation_id": a["allocation_id"], "owner_id": a["owner_id"],
             "owner_kind": a["owner_kind"], "workspace_id": a["workspace_id"],
             "age_seconds": max(0, _now - a["allocated_at"])} for a in rows[:MAX_TABLE]]


if _op == "inventory":
    _emit({"inventory": _observe()})

if _op == "list":
    if not _os.path.isdir(_root):
        _emit({"ranges": [], "allocations": [], "capacity_proof": None, "truncated": False})
    _fd = _lock(False)
    _state = _load()
    _allocs = sorted(_state["allocations"], key=lambda a: (a["allocated_at"], a["allocation_id"]))
    _emit({"ranges": [dict(r, capacity=parse_range(r["cidr"], r["subnet_prefix"]).capacity)
                      for r in _state["ranges"]][:MAX_RANGES],
           "allocations": _allocs[:MAX_LISTED],
           "capacity_proof": _state.get("capacity_proof"),
           "truncated": len(_allocs) > MAX_LISTED})

if _op == "assign":
    try:
        _dev = parse_range(_request.get("cidr"), _request.get("subnet_prefix"))
    except RangeError as exc:
        _refuse(exc.code, str(exc), **exc.data)
    _holder = _request.get("holder")
    if not isinstance(_holder, str) or not _HOLDER.fullmatch(_holder):
        _refuse("range_invalid", "assigning holder is invalid")
    _fd = _lock(True)
    _state = _load()
    try:
        _noop = check_assignment(_dev, inventory_from_payload(_observe()), _ranges(_state))
    except RangeError as exc:
        _refuse(exc.code, str(exc), **exc.data)
    if not _noop:
        if len(_state["ranges"]) >= MAX_RANGES:
            _refuse("range_capacity_reached", "the remote already has the maximum number of ranges")
        _state["ranges"].append({"range_id": _dev.range_id, "cidr": str(_dev.cidr),
                                 "subnet_prefix": _dev.subnet_prefix,
                                 "assigned_at": _now, "assigned_by": _holder})
        _save(_state)
    _emit({"assigned": not _noop, "range_id": _dev.range_id, "capacity": _dev.capacity})

if _op == "allocate":
    _kind = _request.get("owner_kind")
    _owner, _ws = _request.get("owner_id"), _request.get("workspace_id")
    _names = _request.get("networks")
    _pool = _request.get("pool_capacity")
    if (_kind not in ("workspace", "job") or not isinstance(_owner, str) or not _ID.fullmatch(_owner)
            or not isinstance(_ws, str) or not _ID.fullmatch(_ws)
            or (_kind == "workspace" and _owner != _ws)
            or not isinstance(_names, list) or not 0 < len(_names) <= MAX_NETWORKS_PER_REQUEST
            or not all(isinstance(n, str) and _NET.fullmatch(n) for n in _names)
            or len(set(_names)) != len(_names)
            or not (_pool is None or (type(_pool) is int and 0 <= _pool < 2**31))):
        _refuse("range_request_invalid", "allocation request is invalid")
    _fd = _lock(True)
    _state = _load()
    _have = {(a["workspace_id"], a["network"]): a for a in _state["allocations"]}
    _missing = [n for n in _names if (_ws, n) not in _have]
    _new = []
    if _missing and _state["ranges"]:
        _used = [a["subnet"] for a in _state["allocations"]]
        _observed = inventory_from_payload(_observe())
        # Skip subnets a network Sandbox did not allocate already occupies.
        _foreign = [n for n in _observed.networks if str(n) not in set(_used)]
        for _dev, _subnet in free_subnets(_ranges(_state), _used):
            if any(_subnet.overlaps(f) for f in _foreign):
                continue
            _new.append((_dev, _subnet))
            if len(_new) == len(_missing):
                break
    _stats = _range_stats(_state)
    _proof = {"proven_at": _now, "pool_capacity": _pool, "range_capacity": _stats["capacity"],
              "allocated": _stats["allocated"], "usable": _stats["usable"]}
    if _missing and len(_new) < len(_missing):
        _state["capacity_proof"] = _proof
        _save(_state)
        _emit({"granted": [], "exhausted": bool(_state["ranges"]), "no_range": not _state["ranges"],
               "required": len(_missing), "range": _stats, "table": _table(_state["allocations"])})
    _granted = []
    for _name in _names:
        _existing = _have.get((_ws, _name))
        if _existing is None:
            _dev, _subnet = _new.pop(0)
            _seed = "\0".join((_ws, _name, str(_subnet), str(_now), _owner))
            _existing = {"allocation_id": "a-" + hashlib.sha256(_seed.encode()).hexdigest()[:16],
                         "range_id": _dev.range_id, "subnet": str(_subnet),
                         "owner_kind": _kind, "owner_id": _owner, "workspace_id": _ws,
                         "network": _name, "allocated_at": _now}
            _state["allocations"].append(_existing)
        _granted.append({"allocation_id": _existing["allocation_id"], "network": _name,
                         "subnet": _existing["subnet"]})
    _stats = _range_stats(_state)
    _proof.update(allocated=_stats["allocated"], usable=_stats["usable"])
    _state["capacity_proof"] = _proof
    _save(_state)
    _emit({"granted": _granted, "exhausted": False, "no_range": False, "range": _stats})

if _op == "release-owner":
    _owner, _ws = _request.get("owner_id"), _request.get("workspace_id")
    if (_owner is None) == (_ws is None) or not isinstance(_owner or _ws, str) \
            or not _ID.fullmatch(_owner or _ws):
        _refuse("range_request_invalid", "release needs exactly one of owner_id or workspace_id")
    if not _os.path.isdir(_root):
        _emit({"released": 0})
    _fd = _lock(True)
    _state = _load()
    _keep = [a for a in _state["allocations"]
             if not (a["owner_id"] == _owner if _owner else a["workspace_id"] == _ws)]
    _released = len(_state["allocations"]) - len(_keep)
    if _released:
        _state["allocations"] = _keep
        _save(_state)
    _emit({"released": _released})

_refuse("range_request_invalid", "unknown operation")
'''


def program_text() -> str:
    """The ranges module source followed by the driver and its bounds."""
    source = (Path(__file__).with_name("ranges.py")).read_text()
    bounds = (f"\nMAX_RANGES = {MAX_RANGES}\nMAX_LISTED = {MAX_LISTED}\n"
              f"MAX_TABLE = {MAX_TABLE}\nMAX_NETWORKS_PER_REQUEST = {MAX_NETWORKS_PER_REQUEST}\n"
              f"MAX_OBSERVED = {MAX_OBSERVED}\n")
    return source + bounds + _DRIVER


def remote_command(request: dict) -> str:
    payload = base64.b64encode(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).decode()
    return (f"python3 -c {shlex.quote(program_text())} "
            f"\"${{SANDBOX_HOME:-$HOME/sandbox}}\" {shlex.quote(payload)}")
