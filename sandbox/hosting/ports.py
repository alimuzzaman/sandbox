"""Strict host-port observations; no transport selection or mutation.

API: observe_ports(runner, *, identity, runtime_dir) invokes a selected bounded
runner and returns a validated version-1 snapshot. identity is {target, uid, home}
from the caller's current physical target, never a remote alias. validate_snapshot
also accepts serialized snapshots. primary_ports/check_pair consume exact
{project, service, container_port} owners. recheck_command builds a read-only
remote check for use INSIDE root's ingress flocks, followed by && Compose up.
None is never an observation. Offline allocation remains explicitly provisional.
Unknown listeners reserve their entire TCP number. An exact loopback listener
coherent with one exact Docker publication can retain that publication. No environment or raw inspect data
is returned. Snapshots expire after 120 seconds; caller must verify target identity.
"""
from __future__ import annotations

import ipaddress
import json
import re
import shlex
import time
from typing import Callable

MAX_BYTES = 1024 * 1024
MAX_ROWS = 4096
MAX_AGE = 120


class PortObservationError(ValueError):
    pass


def _fail(message):
    raise PortObservationError(message)


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        _fail("invalid TCP port")
    return value


def _text(value):
    if type(value) is not str or not value or len(value) > 512 or any(ord(c) < 32 for c in value):
        _fail("invalid port identity")
    return value


def _identity(value):
    if type(value) is not dict or set(value) != {"target", "uid", "home"}:
        _fail("missing physical target identity")
    _text(value["target"])
    _text(value["home"])
    if type(value["uid"]) is not int or value["uid"] < 0 or not value["home"].startswith("/"):
        _fail("invalid physical target identity")


def _address(value):
    if type(value) is not str:
        _fail("invalid TCP address")
    if value != "*":
        try:
            ipaddress.ip_address(value)
        except (ValueError, TypeError):
            _fail("invalid TCP address")


def _owner(owner):
    if type(owner) is not dict or set(owner) != {"project", "service", "container_port"}:
        _fail("invalid expected port owner")
    _text(owner["project"])
    _text(owner["service"])
    _port(owner["container_port"])


def validate_snapshot(value, *, identity=None, now=None):
    """Validate every field; missing sections and incomplete/truncated frames refuse."""
    if type(value) is str:
        if len(value.encode()) > MAX_BYTES:
            _fail("port observation exceeds byte limit")
        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    _fail("duplicate observation field")
                result[key] = item
            return result
        try:
            value = json.loads(value, object_pairs_hook=pairs)
        except (ValueError, TypeError) as exc:
            raise PortObservationError("invalid port observation JSON") from exc
    if type(value) is not dict or set(value) != {"version", "complete", "identity", "observed_at", "listeners", "publications", "reservations", "end"}:
        _fail("missing port observation frame")
    if type(value["version"]) is not int or value["version"] != 1 or value["complete"] is not True or value["end"] != "sandbox.ports.v1.complete":
        _fail("incomplete port observation")
    _identity(value["identity"])
    if identity is not None and value["identity"] != identity:
        _fail("port observation target mismatch")
    stamp = value["observed_at"]
    if type(stamp) not in (int, float) or not 0 <= (time.time() if now is None else now) - stamp <= MAX_AGE:
        _fail("stale port observation")
    total = 0
    for section in ("listeners", "publications", "reservations"):
        rows = value[section]
        if type(rows) is not list:
            _fail("invalid port observation section")
        total += len(rows)
        seen = set()
        for row in rows:
            fields = {"address", "port"} if section == "listeners" else ({"address", "port", "container", "project", "service", "container_port"} if section == "publications" else {"project", "port"})
            if type(row) is not dict or set(row) != fields:
                _fail("invalid port observation row")
            _port(row["port"])
            if section != "reservations":
                _address(row["address"])
            if section != "listeners":
                _text(row["project"])
            if section == "publications":
                _text(row["service"])
                if type(row["container"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", row["container"]):
                    _fail("invalid container identity")
                _port(row["container_port"])
            encoded = json.dumps(row, sort_keys=True)
            if encoded in seen:
                _fail("duplicate port observation row")
            seen.add(encoded)
    if total > MAX_ROWS or len(json.dumps(value).encode()) > MAX_BYTES:
        _fail("port observation exceeds limits")
    # Two containers claiming one bind is ambiguous even when their labels agree.
    claims = {}
    containers = {}
    for row in value["publications"]:
        claim = (row["port"], row["address"])
        if claim in claims:
            _fail("duplicate Docker bind owner")
        claims[claim] = row["container"]
        if row["port"] in containers and containers[row["port"]] != row["container"]:
            _fail("multiple containers own one TCP port number")
        containers[row["port"]] = row["container"]
    return value


def primary_ports(snapshot, owner):
    snapshot = validate_snapshot(snapshot)
    _owner(owner)
    return {row["port"] for row in snapshot["publications"]
            if all(row[key] == value for key, value in owner.items())}


def check_pair(snapshot, port, owner, *, gate_owner=None, gate_offset=0):
    """Refuse any foreign/unknown bind; never reassign a selected port."""
    snapshot = validate_snapshot(snapshot)
    _owner(owner)
    if type(gate_offset) is not int or gate_offset < 0:
        _fail("invalid gate offset")
    pairs = [(port, owner)]
    if gate_owner is not None:
        _owner(gate_owner)
        if gate_offset == 0 or gate_owner["project"] != owner["project"]:
            _fail("invalid gate owner/offset")
        pairs.append((port + gate_offset, gate_owner))
    for selected, expected in pairs:
        _port(selected)
        own = [row for row in snapshot["publications"]
               if row["port"] == selected and row["address"] == "127.0.0.1"
               and all(row[key] == value for key, value in expected.items())]
        # Docker's published bind and ss's loopback listener describe the same
        # exclusive address/port. Never exempt wildcard or other-address listeners.
        if any(row["port"] == selected and
               (row["address"] != "127.0.0.1" or len(own) != 1)
               for row in snapshot["listeners"]):
            _fail(f"TCP port {selected} has an unknown listener")
        for row in snapshot["publications"]:
            if row["port"] == selected and (row["address"] != "127.0.0.1" or any(row[key] != value for key, value in expected.items())):
                _fail(f"TCP port {selected} has a conflicting Docker owner")
        if any(selected in {row["port"], row["port"] + gate_offset} and row["project"] != owner["project"] for row in snapshot["reservations"]):
            _fail(f"TCP port {selected} is reserved by another target")


# This program is deliberately self-contained: installed target source may differ
# from controller source. Each child has its own checked exit/timeout/byte limit.
_PROBE = r'''
import os, sys, json, time, subprocess, ipaddress, pathlib, re
LIMIT=1048576
ROWS=4096
DEADLINE=time.monotonic()+60
def strict_pairs(items):
    result={}
    for key,value in items:
        if key in result: fail()
        result[key]=value
    return result
def fail(): raise RuntimeError("port observation refused")
def run(args):
    if time.monotonic()>DEADLINE: fail()
    import tempfile
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        p=subprocess.Popen(args, stdout=out, stderr=err)
        deadline=min(DEADLINE,time.monotonic()+15)
        while p.poll() is None:
            if time.monotonic()>deadline or os.fstat(out.fileno()).st_size>LIMIT or os.fstat(err.fileno()).st_size>LIMIT:
                p.kill(); p.wait(); fail()
            time.sleep(.02)
        if p.returncode or os.fstat(out.fileno()).st_size>LIMIT or os.fstat(err.fileno()).st_size>LIMIT: fail()
        out.seek(0)
        return out.read(LIMIT+1).decode("utf-8", "strict")
def collect(identity, runtime):
    if os.getuid()!=identity['uid'] or not pathlib.Path(identity['home']).is_dir(): fail()
    listeners=[]
    for line in run(['ss','-H','-ltn']).splitlines():
        fields=line.split()
        if len(fields)!=5 or fields[0]!='LISTEN' or not fields[1].isdigit() or not fields[2].isdigit(): fail()
        address, sep, port=fields[3].rpartition(':')
        address=address.strip('[]')
        if not sep or not port.isdigit(): fail()
        if address!='*': ipaddress.ip_address(address)
        listeners.append(dict(address=address,port=int(port)))
        if len(listeners)>ROWS: fail()
    ids=run(['docker','ps','--no-trunc','--quiet']).splitlines()
    if len(ids)>ROWS or len(set(ids))!=len(ids): fail()
    publications=[]
    for cid in ids:
        if not re.fullmatch('[0-9a-f]{64}',cid): fail()
        # Allow-listed projections only. Never collect Config.Env or raw inspect.
        fmt='{"id":{{json .Id}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"ports":{{json .NetworkSettings.Ports}}}'
        item=json.loads(run(['docker','inspect','--format',fmt,cid]),object_pairs_hook=strict_pairs)
        if item['ports'] is None: item['ports']={}
        if item['id']!=cid or type(item['ports']) is not dict: fail()
        for target, binds in item['ports'].items():
            match=re.fullmatch(r'([0-9]+)/(tcp|udp|sctp)',target)
            if not match: fail()
            if match[2]!='tcp': continue
            if binds is None: continue
            if type(binds) is not list: fail()
            for bind in binds:
                if set(bind)!={'HostIp','HostPort'} or not bind['HostPort'].isdigit(): fail()
                publications.append(dict(address=bind['HostIp'],port=int(bind['HostPort']),container=cid,project=item['project'] or 'unknown:'+cid,service=item['service'] or 'unknown',container_port=int(match[1])))
                if len(publications)>ROWS: fail()
    # Re-list so disappearing/appearing containers cannot silently truncate facts.
    if set(run(['docker','ps','--no-trunc','--quiet']).splitlines())!=set(ids): fail()
    root=pathlib.Path(runtime)
    if not root.is_dir(): fail()
    reservations=[]
    hosts=root/'hosts'
    if hosts.is_symlink(): fail()
    if hosts.exists():
        projects=list(hosts.iterdir())
        if len(projects)>ROWS: fail()
        for project in projects:
            if time.monotonic()>DEADLINE: fail()
            if project.is_symlink(): fail()
            if not project.is_dir() or not re.fullmatch('[a-z0-9][a-z0-9-]*',project.name): continue
            environments=list(project.iterdir())
            if len(environments)>ROWS: fail()
            for environment in environments:
                if time.monotonic()>DEADLINE: fail()
                if environment.is_symlink(): fail()
                if not environment.is_dir() or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',environment.name): continue
                path=environment/'summary.json'
                if not path.exists(): fail()
                if path.is_symlink() or path.stat().st_size>65536: fail()
                record=json.loads(path.read_text(),object_pairs_hook=strict_pairs)
                if type(record) is not dict or type(record.get('schema')) is not int or record.get('schema')!=1 or record.get('project')!=project.name or record.get('environment')!=environment.name: fail()
                if record.get('compose_project')!='sandbox-host-'+project.name+'-'+environment.name: fail()
                if 'loopback_port' not in record: fail()
                port=record['loopback_port']
                if port is not None: reservations.append(dict(project=record['compose_project'],port=port))
                if len(reservations)>ROWS: fail()
    result=dict(version=1,complete=True,identity=identity,observed_at=time.time(),listeners=listeners,publications=publications,reservations=reservations,end='sandbox.ports.v1.complete')
    if sum(len(result[x]) for x in ('listeners','publications','reservations'))>ROWS: fail()
    return result
'''


def probe_command(*, identity, runtime_dir):
    _identity(identity)
    _text(runtime_dir)
    if not runtime_dir.startswith("/"):
        _fail("runtime directory must be absolute")
    invocation = "\nresult=collect(json.loads(sys.argv[1]),sys.argv[2])\nencoded=json.dumps(result,separators=(',',':'))\nif len(encoded.encode())>LIMIT: fail()\nprint(encoded)\n"
    return shlex.join(["python3", "-c", _PROBE + invocation, json.dumps(identity), runtime_dir])


def observe_ports(runner: Callable, *, identity, runtime_dir):
    """runner(command, timeout=..., max_output=..., terminate_on_output_limit=True)."""
    result = runner(probe_command(identity=identity, runtime_dir=runtime_dir), timeout=90,
                    max_output=MAX_BYTES, terminate_on_output_limit=True)
    if (result.returncode != 0 or getattr(result, "termination_reason", None)
            or getattr(result, "stdout_truncated", False)
            or getattr(result, "stderr_truncated", False)):
        _fail("host port probe failed (underlying output withheld)")
    return validate_snapshot(result.stdout, identity=identity)


def recheck_command(*, identity, runtime_dir, port, owner, gate_owner=None, gate_offset=0):
    """Return a check-only command. Root holds locks across this AND Compose up.

    The same parser/checker source is embedded; no target package import occurs.
    No lock, retry, transport or runtime effect is authorized here.
    """
    import inspect
    _identity(identity)
    _text(runtime_dir)
    if not runtime_dir.startswith("/"):
        _fail("runtime directory must be absolute")
    _port(port)
    _owner(owner)
    if gate_owner is not None:
        _owner(gate_owner)
    # Build from the actual validation functions to keep remote checks identical.
    validators = '\n'.join(inspect.getsource(fn) for fn in
                           (_fail, _port, _text, _identity, _address, _owner,
                            validate_snapshot, check_pair))
    config = dict(identity=identity, runtime=runtime_dir, port=port, owner=owner,
                  gate_owner=gate_owner, gate_offset=gate_offset)
    program = (_PROBE + '\nMAX_BYTES=LIMIT\nMAX_ROWS=ROWS\nMAX_AGE=120\n'
               'class PortObservationError(ValueError): pass\n' + validators +
               '\nc=json.loads(sys.argv[1])\ns=collect(c["identity"],c["runtime"])\n'
               'check_pair(s,c["port"],c["owner"],gate_owner=c["gate_owner"],gate_offset=c["gate_offset"])\n')
    return shlex.join(["python3", "-c", program, json.dumps(config)])
