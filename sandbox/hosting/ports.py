"""Strict host-port observations; no transport selection or mutation.

API: observe_ports(runner, *, identity, runtime_dir) invokes a selected bounded
runner and returns a validated version-1 snapshot. identity is {target, uid, home}
from the caller's current physical target, never a remote alias. validate_snapshot
also accepts serialized snapshots. primary_ports/check_pair consume exact
{project, service, container_port} owners. own_projects scopes the strict
container scan to the applied target; other targets only claim ports. recheck_command builds a read-only
remote check for use INSIDE root's ingress flocks, followed by && Compose up.
None is never an observation. Offline allocation remains explicitly provisional.
Unknown listeners reserve their entire TCP number. A listener exception requires
a root-verified docker-proxy socket, process and container destination. No environment or raw inspect data
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
    if value.startswith('['):
        closing = value.find(']')
        if closing < 0:
            _fail("invalid TCP brackets")
        body, suffix = value[1:closing], value[closing + 1:]
        if suffix and (not suffix.startswith('%') or '%' in body):
            _fail("invalid TCP scope")
        value = body + suffix
    elif '[' in value or ']' in value:
        _fail("invalid TCP brackets")
    if value != "*":
        address, separator, interface = value.partition('%')
        if separator and not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,64}', interface):
            _fail("invalid TCP interface")
        try:
            return str(ipaddress.ip_address(address))
        except (ValueError, TypeError):
            _fail("invalid TCP address")
    return value


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
    if type(value) is not dict or set(value) != {"version", "complete", "identity", "engine_id", "observed_at", "listeners", "publications", "reservations", "end"}:
        _fail("missing port observation frame")
    if type(value["version"]) is not int or value["version"] != 1 or value["complete"] is not True or value["end"] != "sandbox.ports.v1.complete":
        _fail("incomplete port observation")
    _identity(value["identity"])
    _text(value["engine_id"])
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
            fields = {"address", "port", "container"} if section == "listeners" else ({"address", "port", "container", "project", "service", "container_port", "container_addresses"} if section == "publications" else {"project", "port"})
            if type(row) is not dict or set(row) != fields:
                _fail("invalid port observation row")
            _port(row["port"])
            if section != "reservations":
                row["address"] = _address(row["address"])
            if section == "listeners" and row["container"] is not None:
                if type(row["container"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", row["container"]):
                    _fail("invalid socket ownership proof")
            if section != "listeners":
                _text(row["project"])
            if section == "publications":
                _text(row["service"])
                if type(row["container"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", row["container"]):
                    _fail("invalid container identity")
                _port(row["container_port"])
                addresses = row["container_addresses"]
                if type(addresses) is not list or len(addresses) > 128 or any(
                        type(address) is not str or _address(address) != address or address == '*'
                        for address in addresses):
                    _fail("invalid container addresses")
            encoded = json.dumps(row, sort_keys=True)
            if encoded in seen:
                _fail("duplicate port observation row")
            seen.add(encoded)
    if total > MAX_ROWS or len(json.dumps(value).encode()) > MAX_BYTES:
        _fail("port observation exceeds limits")
    return value


def _single_owner(snapshot, port):
    """Two containers claiming one TCP number make that number ambiguous.

    Scoped to the number being decided: another target's mid-recreate
    claims must not refuse an unrelated port.
    """
    claims = set()
    containers = set()
    for row in snapshot["publications"]:
        if row["port"] != port:
            continue
        if row["address"] in claims:
            _fail("duplicate Docker bind owner")
        claims.add(row["address"])
        containers.add(row["container"])
    if len(containers) > 1:
        _fail("multiple containers own one TCP port number")


def primary_ports(snapshot, owner):
    snapshot = validate_snapshot(snapshot)
    _owner(owner)
    found = {row["port"] for row in snapshot["publications"]
             if all(row[key] == value for key, value in owner.items())}
    for port in found:
        _single_owner(snapshot, port)
    return found


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
        _single_owner(snapshot, selected)
        own = [row for row in snapshot["publications"]
               if row["port"] == selected and row["address"] == "127.0.0.1"
               and all(row[key] == value for key, value in expected.items())]
        # Equal Docker metadata is not socket ownership: NAT can coexist with
        # a foreign native listener. Only the collector's kernel/proxy proof
        # may associate this socket with the exact expected container.
        if any(row["port"] == selected and
               (row["address"] != "127.0.0.1" or len(own) != 1
                or row["container"] != own[0]["container"])
               for row in snapshot["listeners"]):
            _fail(f"TCP port {selected} has an unknown listener")
        for row in snapshot["publications"]:
            if row["port"] == selected and (row["address"] != "127.0.0.1" or any(row[key] != value for key, value in expected.items())):
                _fail(f"TCP port {selected} has a conflicting Docker owner")
        if any(selected in {row["port"], row["port"] + gate_offset} and row["project"] != owner["project"] for row in snapshot["reservations"]):
            _fail(f"TCP port {selected} is reserved by another target")


# This program is deliberately self-contained: installed target source may differ
# from controller source. Each child has its own checked exit/timeout/byte limit.
_SOCKET_PROBE = r'''
import os,sys,json,re,stat,ipaddress,subprocess,tempfile,time
LIMIT=1048576
if os.geteuid()!=0: raise RuntimeError('socket observation requires root')
raw=sys.stdin.buffer.read(LIMIT+1)
if len(raw)>LIMIT: raise RuntimeError('socket input exceeds bound')
publications=json.loads(raw)
def address(value):
    if value.startswith('['):
        closing=value.find(']')
        if closing<0: raise ValueError()
        body,suffix=value[1:closing],value[closing+1:]
        if suffix and (not suffix.startswith('%') or '%' in body): raise ValueError()
        value=body+suffix
    elif '[' in value or ']' in value: raise ValueError()
    base,sep,interface=value.partition('%')
    if sep and not re.fullmatch('[A-Za-z0-9_.:@-]{1,64}',interface): raise ValueError()
    return '*' if base=='*' else str(ipaddress.ip_address(base))
def read_at(parent,name):
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
    try:
        value=os.read(fd,8193)
        if len(value)>8192: raise ValueError()
        return value
    finally: os.close(fd)
def proxy_container(tail,host,port):
    candidates=re.findall(r'pid=([0-9]+),fd=([0-9]+)',tail)
    inode=re.search(r'\bino:([0-9]+)\b',tail)
    if len(candidates)!=1 or inode is None: return None
    pid,fd=candidates[0]
    proc=None
    try:
        proc=os.open('/proc/'+pid,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW)
        exe=os.readlink('exe',dir_fd=proc)
        info=os.stat('exe',dir_fd=proc)
        disk=os.stat(exe,follow_symlinks=False)
        if (exe.rsplit('/',1)[-1]!='docker-proxy' or info.st_uid!=0 or info.st_mode&0o022
                or not stat.S_ISREG(disk.st_mode)
                or (info.st_dev,info.st_ino)!=(disk.st_dev,disk.st_ino)): return None
        before=read_at(proc,'stat')
        status=read_at(proc,'status').decode()
        if not re.search(r'^Uid:\s+0\s+0\s+0\s+0$',status,re.M): return None
        socket='socket:['+inode[1]+']'
        if os.readlink('fd/'+fd,dir_fd=proc)!=socket: return None
        args=read_at(proc,'cmdline').decode().rstrip('\0').split('\0')
        fields={}; index=1
        while index<len(args):
            flag=args[index]
            if flag in ('-use-listen-fd','-use-listen-fd=true'):
                index+=1; continue
            if flag not in ('-proto','-host-ip','-host-port','-container-ip','-container-port') or flag in fields or index+1>=len(args): return None
            fields[flag]=args[index+1]; index+=2
        if (set(fields)!=set(('-proto','-host-ip','-host-port','-container-ip','-container-port'))
            or fields['-proto']!='tcp' or address(fields['-host-ip'])!=host
            or int(fields['-host-port'])!=port): return None
        matches=[row['container'] for row in publications
            if row['address']==host and row['port']==port
            and int(fields['-container-port'])==row['container_port']
            and address(fields['-container-ip']) in row['container_addresses']]
        # The held proc directory and same live socket prevent PID reuse from
        # turning a stale ss row into another process's ownership proof.
        if (len(matches)!=1 or os.readlink('exe',dir_fd=proc)!=exe
                or os.readlink('fd/'+fd,dir_fd=proc)!=socket
                or read_at(proc,'stat').rsplit(b')',1)[1].split()[19]
                   !=before.rsplit(b')',1)[1].split()[19]): return None
        return matches[0]
    except (OSError,ValueError,IndexError,UnicodeError): return None
    finally:
        if proc is not None: os.close(proc)
with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
    child=subprocess.Popen(['ss','-H','-ltnpe'],stdout=out,stderr=err)
    deadline=time.monotonic()+10
    while child.poll() is None:
        if time.monotonic()>deadline or os.fstat(out.fileno()).st_size>LIMIT or os.fstat(err.fileno()).st_size>LIMIT:
            child.kill();child.wait();raise RuntimeError('socket probe incomplete')
        time.sleep(.02)
    if child.returncode or os.fstat(out.fileno()).st_size>LIMIT or os.fstat(err.fileno()).st_size>LIMIT: raise RuntimeError('socket probe failed')
    out.seek(0); lines=out.read(LIMIT+1).decode().splitlines()
rows=[]
for line in lines:
    fields=line.split(None,5)
    if len(fields)<5 or fields[0]!='LISTEN' or not fields[1].isdigit() or not fields[2].isdigit(): raise ValueError()
    host,sep,port=fields[3].rpartition(':')
    if not sep or not port.isdigit(): raise ValueError()
    host=address(host);port=int(port)
    rows.append(dict(address=host,port=port,container=proxy_container(fields[5] if len(fields)>5 else '',host,port)))
    if len(rows)>4096: raise ValueError()
encoded=json.dumps(rows)
if len(encoded.encode())>LIMIT: raise ValueError()
print(encoded)
'''

_PROBE = 'SOCKET_PROGRAM=' + repr(_SOCKET_PROBE) + '\n' + r'''
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
# Failures name a fixed stage and kind only, never Docker output, so the
# controller can relay them (STAGE is set by collect/scan as they progress).
STAGE=['start']
class Refused(RuntimeError): pass
def fail(kind='invalid'): raise Refused('port observation refused: '+STAGE[0]+'/'+kind)
def attempt(args, payload=None, budget=15):
    if time.monotonic()>DEADLINE: fail('deadline')
    import tempfile
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as source:
        if payload is not None: source.write(payload.encode()); source.seek(0)
        p=subprocess.Popen(args, stdout=out, stderr=err, stdin=source if payload is not None else subprocess.DEVNULL)
        deadline=min(DEADLINE,time.monotonic()+budget)
        while p.poll() is None:
            if time.monotonic()>deadline or os.fstat(out.fileno()).st_size>LIMIT or os.fstat(err.fileno()).st_size>LIMIT:
                p.kill(); p.wait(); fail('deadline' if time.monotonic()>DEADLINE else 'timeout' if time.monotonic()>deadline else 'output_limit')
            time.sleep(.02)
        if os.fstat(out.fileno()).st_size>LIMIT or os.fstat(err.fileno()).st_size>LIMIT: fail('output_limit')
        out.seek(0); err.seek(0)
        return p.returncode, out.read(LIMIT+1).decode("utf-8", "strict"), err.read(LIMIT+1).decode("utf-8", "strict")
def run(args, payload=None):
    code,out,_=attempt(args, payload)
    if code: fail('exit')
    return out
def vanished(cid, code, err):
    # Only Docker's own not-found reply for this exact id counts as removal.
    # Any other failure (daemon, permission, timeout, extra output) refuses.
    lines=[line.strip() for line in err.splitlines() if line.strip()]
    return code!=0 and len(lines)==1 and re.fullmatch('(?i)(error: no such object|error response from daemon: no such container): '+cid,lines[0]) is not None
class Churn(Exception): pass
SCANS=8
FORMAT='{"id":{{json .Id}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"ports":{{json .NetworkSettings.Ports}},"addresses":"{{range .NetworkSettings.Networks}}{{.IPAddress}},{{.GlobalIPv6Address}},{{end}}"}'
LIST_FORMAT='[{{json .ID}},{{json (.Label "com.docker.compose.project")}}]'
def listed():
    """Running container id -> its Compose project label ('' when unlabeled)."""
    lines=run(['docker','ps','--no-trunc','--format',LIST_FORMAT]).splitlines()
    if len(lines)>ROWS: fail()
    found={}
    for line in lines:
        item=json.loads(line,object_pairs_hook=strict_pairs)
        if type(item) is not list or len(item)!=2 or type(item[1]) is not str: fail()
        cid=item[0]
        if type(cid) is not str or not re.fullmatch('[0-9a-f]{64}',cid) or cid in found: fail()
        found[cid]=item[1]
    return found
def publication_rows(item, cid):
    if item['ports'] is None: item['ports']={}
    if item['id']!=cid or type(item['ports']) is not dict: fail()
    rows=[]
    for target, binds in item['ports'].items():
        match=re.fullmatch(r'([0-9]+)/(tcp|udp|sctp)',target)
        if not match: fail()
        if match[2]!='tcp': continue
        if binds is None: continue
        if type(binds) is not list: fail()
        for bind in binds:
            if set(bind)!={'HostIp','HostPort'} or not bind['HostPort'].isdigit(): fail()
            addresses=sorted(set(str(ipaddress.ip_address(value)) for value in item['addresses'].split(',') if value))
            rows.append(dict(address=bind['HostIp'],port=int(bind['HostPort']),container=cid,project=item['project'] or 'unknown:'+cid,service=item['service'] or 'unknown',container_port=int(match[1]),container_addresses=addresses))
            if len(rows)>ROWS: fail()
    return rows
def inspect_many(ids, form=None):
    """One Docker call for every id: {id: item or None when Docker says it is gone}.

    Allow-listed projections only. Never collect Config.Env or raw inspect.
    Only Docker's own per-id not-found lines may accompany a failed exit.
    """
    if not ids: return {}
    # The CLI looks ids up one after another: the budget grows with the batch.
    code,out,err=attempt(['docker','inspect','--type','container','--format',form or FORMAT,*ids],
                         budget=15+0.25*len(ids))
    items={}
    for line in out.splitlines():
        if not line.strip(): continue
        item=json.loads(line,object_pairs_hook=strict_pairs)
        cid=item['id'] if type(item) is dict else item
        if type(cid) is not str or cid not in ids or cid in items: fail()
        items[cid]=item
    gone=[cid for cid in ids if cid not in items]
    lines=[line.strip() for line in err.splitlines() if line.strip()]
    if code==0:
        if gone or lines: fail()
        return items
    # Each missing id needs exactly one not-found line, in any order, and nothing else.
    if not gone or len(lines)!=len(gone) or len(set(lines))!=len(lines) or not all(
            any(vanished(cid,1,line) for line in lines) for cid in gone): fail('docker_error')
    return dict(items,**{cid:None for cid in gone})
def scan(own):
    # Spec: an apply observes only its own target strictly. Containers of the
    # own Compose projects follow the full rule below; any churn among them
    # restarts the scan. Every other container is read in ONE batched inspect
    # and only claims ports: one removed mid-scan (another target's recreate)
    # is dropped instead of restarting, so foreign applies cannot exhaust the
    # scan budget. Facts cover every container running in the first list OR
    # the re-list. An own container removed outright restarts the scan; one
    # that leaves the running list but still exists (a restart loop) keeps its
    # host ports only as an unknown-owner claim with no container addresses:
    # stale facts can refuse a port but never prove socket ownership.
    STAGE[0]='container_list'
    first=listed()
    rows={}
    STAGE[0]='container_inspect'
    def take(ids):
        for cid,item in inspect_many(ids).items():
            if item is None:
                if mine.get(cid): raise Churn()
                continue
            rows[cid]=publication_rows(item,cid)
    mine={cid:(project in own) for cid,project in first.items()}
    take(list(first))
    STAGE[0]='container_relist'
    second=listed()
    STAGE[0]='container_inspect'
    entered=[cid for cid in second if cid not in first]
    mine.update({cid:(second[cid] in own) for cid in entered})
    take(entered)
    departed=[cid for cid in first if cid not in second and cid in rows]
    for cid,item in inspect_many(departed,'{"id":{{json .Id}}}').items():
        if item is None and mine[cid]: raise Churn()
        # Gone or merely stopped, another target's departed container keeps its
        # ports as an unknown-owner claim: a replacement may bind them after the
        # re-list. A stale claim can refuse a port but never prove ownership.
        rows[cid]=[dict(row,project='unknown:'+cid,service='unknown',container_addresses=[]) for row in rows[cid]]
    publications=[row for cid in rows for row in rows[cid]]
    if len(publications)>ROWS: fail()
    return publications
def settled_scan(own):
    # Facts come only from one scan whose own inspects all succeeded; own
    # churn that never settles within SCANS refuses.
    STAGE[0]='own_projects'
    if type(own) is not list or not own or any(type(p) is not str or not p for p in own): fail()
    own=set(own)
    for _ in range(SCANS):
        try:
            return scan(own)
        except Churn:
            continue
    STAGE[0]='own_containers'
    fail('unsettled')
def collect(identity, runtime, own):
    STAGE[0]='identity'
    if os.getuid()!=identity['uid'] or not pathlib.Path(identity['home']).is_dir(): fail()
    STAGE[0]='docker_info'
    engine=json.loads(run(['docker','info','--format','{{json .ID}}']))
    if type(engine) is not str or not engine or len(engine)>512: fail()
    publications=settled_scan(own)
    STAGE[0]='socket_probe'
    socket_args=['python3','-I','-S','-c',SOCKET_PROGRAM]
    if os.geteuid()!=0: socket_args=['sudo','-n',*socket_args]
    listeners=json.loads(run(socket_args,json.dumps(publications)),object_pairs_hook=strict_pairs)
    STAGE[0]='docker_info'
    if json.loads(run(['docker','info','--format','{{json .ID}}']))!=engine: fail('engine_changed')
    STAGE[0]='host_summaries'
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
                if path.is_symlink(): fail()
                if not path.exists():
                    # Pre-panel applies left only this generated primary-port
                    # override. It reserves a number, never proves ownership.
                    legacy=environment/'compose.override.yml'
                    fd=os.open(legacy,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                    try:
                        import stat
                        info=os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_uid!=identity['uid'] or info.st_nlink!=1 or info.st_size>65536: fail()
                        with os.fdopen(fd,'r',closefd=False) as handle: text=handle.read(65537)
                        match=re.fullmatch(r'services:\n  ([A-Za-z0-9][A-Za-z0-9_.-]*):\n    ports:\n      - "127\.0\.0\.1:([0-9]{1,5}):([0-9]{1,5})"\n',text)
                        if not match or not all(1<=int(match[i])<=65535 for i in (2,3)): fail()
                    finally:
                        os.close(fd)
                    reservations.append(dict(project='sandbox-host-'+project.name+'-'+environment.name,port=int(match[2])))
                    if len(reservations)>ROWS: fail()
                    continue
                if path.is_symlink() or path.stat().st_size>65536: fail()
                record=json.loads(path.read_text(),object_pairs_hook=strict_pairs)
                if type(record) is not dict or type(record.get('schema')) is not int or record.get('schema')!=1 or record.get('project')!=project.name or record.get('environment')!=environment.name: fail()
                if record.get('compose_project')!='sandbox-host-'+project.name+'-'+environment.name: fail()
                if 'loopback_port' not in record: fail()
                port=record['loopback_port']
                if port is not None: reservations.append(dict(project=record['compose_project'],port=port))
                if len(reservations)>ROWS: fail()
    result=dict(version=1,complete=True,identity=identity,engine_id=engine,observed_at=time.time(),listeners=listeners,publications=publications,reservations=reservations,end='sandbox.ports.v1.complete')
    if sum(len(result[x]) for x in ('listeners','publications','reservations'))>ROWS: fail()
    return result
'''


# Remote failure line: the probe's own fixed reason, or a port-check refusal
# (fixed text plus a TCP number), or a stage plus an exception class name.
# The controller relays only a line matching REASON exactly.
REASON_PREFIX = "sandbox-port-probe: "
REASON = re.compile(r"sandbox-port-probe: ([a-z_]{1,32})/([A-Za-z0-9_ ]{1,80})")
_REPORT = (
    "except Exception as exc:\n"
    "    if isinstance(exc,Refused): kind=str(exc).rsplit('/',1)[1]\n"
    "    elif type(exc).__name__=='PortObservationError': kind=str(exc)\n"
    "    else: kind=type(exc).__name__\n"
    "    if not re.fullmatch('[A-Za-z0-9_ ]{1,80}',kind): kind='invalid'\n"
    "    sys.stderr.write('" + REASON_PREFIX + "'+STAGE[0]+'/'+kind+'\\n'); sys.exit(3)\n")


def refusal_reason(stderr) -> str | None:
    """The probe's fixed failure line, or None; never other remote output."""
    if type(stderr) is not str:
        return None
    for line in reversed(stderr.splitlines()[-20:]):
        match = REASON.fullmatch(line.strip())
        if match:
            return f"{match[1]}/{match[2]}"
    return None


def _own_projects(projects):
    if (type(projects) not in (list, tuple) or not projects or len(projects) > 8
            or len(set(projects)) != len(projects)):
        _fail("invalid own Compose projects")
    for project in projects:
        _text(project)
    return list(projects)


def probe_command(*, identity, runtime_dir, own_projects):
    _identity(identity)
    own_projects = _own_projects(own_projects)
    _text(runtime_dir)
    if not runtime_dir.startswith("/"):
        _fail("runtime directory must be absolute")
    invocation = ("\ntry:\n    result=collect(json.loads(sys.argv[1]),sys.argv[2],json.loads(sys.argv[3]))\n"
                  "    STAGE[0]='output'\n"
                  "    encoded=json.dumps(result,separators=(',',':'))\n"
                  "    if len(encoded.encode())>LIMIT: fail('output_limit')\n"
                  + _REPORT + "print(encoded)\n")
    return shlex.join(["python3", "-c", _PROBE + invocation, json.dumps(identity), runtime_dir,
                       json.dumps(own_projects)])


def observe_ports(runner: Callable, *, identity, runtime_dir, own_projects):
    """runner(command, timeout=..., max_output=..., terminate_on_output_limit=True).

    own_projects: the Compose projects of the target being applied; only
    their containers are scanned strictly (see the probe's scan()).
    """
    result = runner(probe_command(identity=identity, runtime_dir=runtime_dir,
                                  own_projects=own_projects), timeout=90,
                    max_output=MAX_BYTES, terminate_on_output_limit=True)
    if (result.returncode != 0 or getattr(result, "termination_reason", None)
            or getattr(result, "stdout_truncated", False)
            or getattr(result, "stderr_truncated", False)):
        reason = refusal_reason(getattr(result, "stderr", None))
        if reason is None and getattr(result, "termination_reason", None) == "timeout":
            reason = "transport/timeout"
        _fail("host port probe failed (" + (reason or "reason unavailable") + "; other output withheld)")
    return validate_snapshot(result.stdout, identity=identity)


def recheck_command(*, identity, runtime_dir, port, owner, engine_id, gate_owner=None, gate_offset=0):
    """Return a check-only command. Root holds locks across this AND Compose up.

    The same parser/checker source is embedded; no target package import occurs.
    No lock, retry, transport or runtime effect is authorized here.
    """
    import inspect
    _identity(identity)
    _text(engine_id)
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
                            validate_snapshot, _single_owner, check_pair))
    config = dict(identity=identity, engine_id=engine_id, runtime=runtime_dir, port=port, owner=owner,
                  gate_owner=gate_owner, gate_offset=gate_offset)
    program = (_PROBE + '\nMAX_BYTES=LIMIT\nMAX_ROWS=ROWS\nMAX_AGE=120\n'
               'class PortObservationError(ValueError): pass\n' + validators +
               '\nc=json.loads(sys.argv[1])\ntry:\n'
               '    s=collect(c["identity"],c["runtime"],[c["owner"]["project"]])\n'
               '    if s["engine_id"]!=c["engine_id"]: _fail("Docker daemon changed")\n'
               '    STAGE[0]="port_check"\n'
               '    check_pair(s,c["port"],c["owner"],gate_owner=c["gate_owner"],gate_offset=c["gate_offset"])\n'
               + _REPORT)
    return shlex.join(["python3", "-c", program, json.dumps(config)])


LOCK_BUSY_EXIT = 75  # flock -E: the lock stayed held for the whole wait


def lock_context_command(command, *, uid, locks, wait=30):
    """Keep selected account/groups and a fixed Docker environment across sudo.

    Values travel on anonymous stdin, never argv or a named file. The receiver
    drops root before both the daemon-bound recheck and the original Compose up.
    A lock still held after ``wait`` seconds exits LOCK_BUSY_EXIT, never runs
    ``command``.
    """
    if (type(uid) is not int or uid < 0 or not 1 <= len(locks) <= 2
            or type(wait) is not int or not 1 <= wait <= 86400):
        _fail("invalid lock context")
    for path in locks:
        if type(path) is not str or not path.startswith('/run/lock/'):
            _fail("invalid lock path")
    keys = ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'SANDBOX_HOME',
            'PYTHONNOUSERSITE', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
            'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_TLS_VERIFY',
            'DOCKER_CERT_PATH', 'DOCKER_API_VERSION', 'COMPOSE_PARALLEL_LIMIT')
    receiver = r'''
import json,os,sys
expected=int(sys.argv[1]); command=sys.argv[2]; allowed=json.loads(sys.argv[3])
raw=sys.stdin.buffer.read(65537)
if len(raw)>65536 or os.geteuid()!=0: raise RuntimeError('lock context unavailable')
c=json.loads(raw)
if type(c) is not dict or set(c)!={'uid','gid','groups','environment','cwd','umask'}: raise ValueError()
if type(c['uid']) is not int or c['uid']!=expected or type(c['gid']) is not int or c['gid']<0: raise ValueError()
if type(c['groups']) is not list or len(c['groups'])>256 or any(type(g) is not int or g<0 for g in c['groups']): raise ValueError()
if type(c['cwd']) is not str or not c['cwd'].startswith('/') or '\0' in c['cwd'] or len(c['cwd'])>8192: raise ValueError()
if type(c['umask']) is not int or not 0<=c['umask']<=0o777: raise ValueError()
e=c['environment']
if (type(e) is not dict or set(e)-set(allowed) or not e.get('HOME') or not e.get('PATH')
    or any(type(v) is not str or '\0' in v or len(v)>8192 for v in e.values())): raise ValueError()
os.setgroups(c['groups']);os.setgid(c['gid']);os.setuid(c['uid'])
if os.geteuid()!=expected or os.getegid()!=c['gid'] or sorted(os.getgroups())!=sorted(c['groups']): raise RuntimeError('account restoration failed')
os.chdir(c['cwd']);os.umask(c['umask'])
os.execve('/bin/sh',['sh','-c',command],e)
'''
    locked = [part for path in locks
              for part in ('flock', '-w', str(wait), '-E', str(LOCK_BUSY_EXIT), path)]
    locked += ['python3', '-I', '-S', '-c', receiver, str(uid), command, json.dumps(keys)]
    sender = r'''
import json,os,subprocess,sys
uid=int(sys.argv[1]); allowed=json.loads(sys.argv[2]); argv=json.loads(sys.argv[3])
if os.geteuid()!=uid or os.getuid()!=uid: raise RuntimeError('observed account changed')
# Only fixed context keys are accessed. Provider/application values are neither
# enumerated nor copied. The complete context stays in memory and anonymous stdin.
environment={key:os.environ[key] for key in allowed if key in os.environ}
if not environment.get('HOME') or not environment.get('PATH'): raise RuntimeError('execution context missing')
mask=os.umask(0o077);os.umask(mask)
c=dict(uid=uid,gid=os.getegid(),groups=os.getgroups(),environment=environment,cwd=os.getcwd(),umask=mask)
payload=json.dumps(c).encode()
if len(payload)>65536: raise RuntimeError('execution context exceeds bound')
if uid!=0: argv=['sudo','-n',*argv]
code=subprocess.run(argv,input=payload,check=False).returncode
raise SystemExit(128-code if code<0 else code)
'''
    return shlex.join(['python3', '-c', sender, str(uid), json.dumps(keys), json.dumps(locked)])
