"""Fixed registered-remote transport for locally built hosted images."""
from __future__ import annotations

import json
import os
import posixpath
import re
import selectors
import shlex
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from sandbox.core import _remote
from sandbox.hosting import local_images

_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class RemoteLocalImageError(RuntimeError):
    def __init__(self, code: str, message: str = "local image transport failed"):
        self.code = code
        super().__init__(f"{code}: {message}")


def _entry(remote_name: str) -> dict:
    if not isinstance(remote_name, str) or _REMOTE.fullmatch(remote_name) is None:
        raise RemoteLocalImageError("local_images_remote_invalid")
    entry = _remote.get_remote(remote_name)
    if not isinstance(entry, dict) or entry.get("provisioned") is not True:
        raise RemoteLocalImageError("local_images_remote_unavailable")
    return entry


def _checked(entry: dict, command: str, *, timeout: int) -> str:
    result = _remote.ssh_run(entry, command, timeout=timeout)
    if result.returncode != 0:
        raise RemoteLocalImageError("local_images_remote_failed")
    output = result.stdout or ""
    if len(output.encode("utf-8")) > local_images.MAX_CONFIG_BYTES:
        raise RemoteLocalImageError("local_images_remote_output_too_large")
    return output


def _receipt_root(entry: dict) -> str:
    home = _remote.resolve_sandbox_home(entry)
    if not isinstance(home, str) or not home.startswith("/"):
        raise RemoteLocalImageError("local_images_remote_unavailable")
    return f"{home}/runtime/host-local-images/receipts"


def _local_image_root(entry: dict) -> str:
    home = _remote.resolve_sandbox_home(entry)
    if not isinstance(home, str) or not home.startswith("/"):
        raise RemoteLocalImageError("local_images_remote_unavailable")
    return f"{home}/runtime/host-local-images"


def receipt_path(entry: dict, digest: str) -> str:
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise RemoteLocalImageError("local_images_receipt_invalid")
    return f"{_receipt_root(entry)}/{digest.split(':', 1)[1]}.json"


def read_receipt(remote_name: str, digest: str, *, timeout: int = 60,
                 entry: dict | None = None) -> dict:
    entry = _entry(remote_name) if entry is None else entry
    path = receipt_path(entry, digest)
    program = r'''import json,os,stat,sys
p=sys.argv[1]
try:
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW); info=os.fstat(fd)
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_size>262144: sys.exit(51)
 raw=os.read(fd,262145);os.close(fd)
 if len(raw)>262144: sys.exit(52)
 def closed(pairs):
  result={}
  for key,value in pairs:
   if key in result: raise ValueError('duplicate')
   result[key]=value
  return result
 doc=json.loads(raw,object_pairs_hook=closed);print(json.dumps(doc,sort_keys=True,separators=(',',':')))
except Exception: sys.exit(53)
'''
    output = _checked(entry, shlex.join(["python3", "-c", program, path]), timeout=timeout)
    try:
        document = json.loads(output)
    except ValueError:
        raise RemoteLocalImageError("local_images_receipt_invalid") from None
    if not isinstance(document, dict):
        raise RemoteLocalImageError("local_images_receipt_invalid")
    expected = {"schema", "receipt_digest", "project", "environment", "commit",
                "source_revision", "platform", "verified_at", "local_receipt",
                "images", "machine_identity", "registered_host_digest",
                "daemon_identity", "remote_receipt_digest"}
    if set(document) != expected:
        raise RemoteLocalImageError("local_images_receipt_invalid")
    local_images.validate_receipt(document.get("local_receipt"))
    body = {key: value for key, value in document.items()
            if key != "remote_receipt_digest"}
    if document.get("remote_receipt_digest") != digest \
            or local_images.canonical_digest(body) != digest \
            or document.get("schema") != local_images.remote_schema(document.get("local_receipt") or {}):
        raise RemoteLocalImageError("local_images_receipt_invalid")
    local_images.validate_remote_v2(document)
    return document


def observe(remote_name: str, receipt: dict, *, remote_receipt_digest: str | None = None,
            timeout: int = 120, entry: dict | None = None) -> dict:
    """Observe engine, exact references, and optionally one remote receipt."""
    entry = _entry(remote_name) if entry is None else entry
    refs = sorted({artifact_ref for artifact in receipt["artifacts"].values()
                   for artifact_ref in artifact["refs"]} | set(local_images.pinned_refs(receipt)))
    path = receipt_path(entry, remote_receipt_digest) if remote_receipt_digest else None
    argv = local_images.probe_argv(refs=refs, engine=True, receipt_path=path)
    output = _checked(entry, shlex.join(argv), timeout=timeout)
    return local_images.parse_probe_output(output)


_STAGE_PROGRAM = r'''import fcntl,hashlib,json,os,stat,subprocess,sys,tempfile
root,name,want,size=sys.argv[1:]; size=int(size)
if not root.startswith('/') or '/' in name or name in {'.','..'}: sys.exit(31)
os.makedirs(root,mode=0o700,exist_ok=True)
ri=os.lstat(root)
if not stat.S_ISDIR(ri.st_mode) or ri.st_uid!=os.geteuid() or stat.S_IMODE(ri.st_mode)&0o002: sys.exit(31)
os.chmod(root,0o700)
# The worker owns this fence even if its controller's SSH lease disappears.
fence=os.open(os.path.dirname(root)+'/load-fence.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
fi=os.fstat(fence)
if not stat.S_ISREG(fi.st_mode) or fi.st_uid!=os.geteuid() or stat.S_IMODE(fi.st_mode)!=0o600 or fi.st_nlink!=1: sys.exit(31)
try: fcntl.flock(fence,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError: sys.exit(75)
fd,path=tempfile.mkstemp(prefix='.upload-',suffix='.tar',dir=root); h=hashlib.sha256(); count=0
try:
 os.fchmod(fd,0o600)
 while count<=size:
  chunk=os.read(0,min(1048576,size+1-count))
  if not chunk: break
  os.write(fd,chunk);h.update(chunk);count+=len(chunk)
 os.fsync(fd);os.close(fd);fd=-1
 if count!=size or 'sha256:'+h.hexdigest()!=want: sys.exit(32)
 q=subprocess.run(['docker','image','load','-i',path],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1800,check=False,pass_fds=(fence,))
 if q.returncode: sys.exit(33)
 print(json.dumps({'schema':1,'state':'loaded','bytes':count},separators=(',',':')))
finally:
 if fd>=0: os.close(fd)
 try: os.unlink(path)
 except OSError: pass
'''


def load_archive(remote_name: str, archive: Path, artifact: dict, *, timeout: int,
                 progress=None, cancelled=None, entry: dict | None = None) -> dict:
    """Verify the complete staged bytes before invoking the fixed Docker load."""
    started = time.monotonic()
    entry = _entry(remote_name) if entry is None else entry
    archive = Path(archive)
    try:
        info = archive.lstat()
    except OSError:
        raise RemoteLocalImageError("local_images_archive_unavailable") from None
    if archive.is_symlink() or not stat.S_ISREG(info.st_mode) \
            or info.st_uid != os.geteuid() or info.st_nlink != 1 \
            or stat.S_IMODE(info.st_mode) & 0o077:
        raise RemoteLocalImageError("local_images_archive_unsafe")
    digest, size = local_images.file_sha256(archive)
    if digest != artifact["archive_sha256"] or size != artifact["archive_bytes"]:
        raise RemoteLocalImageError("local_images_archive_changed")
    root = f"{_local_image_root(entry)}/uploads"
    argv = ["python3", "-c", _STAGE_PROGRAM, root, artifact["archive"], digest, str(size)]
    timeout -= time.monotonic() - started
    if timeout <= 0:
        raise RemoteLocalImageError("local_images_transfer_unknown")
    result = local_images.stream_archive(
        _remote.ssh_command_args(entry, shlex.join(argv), multiplex=False),
        archive, timeout=timeout, progress=progress, cancelled=cancelled)
    if result["timed_out"] or result["cancelled"] or result["broken_pipe"]:
        raise RemoteLocalImageError("local_images_transfer_unknown")
    if result["returncode"] != 0 or result["sha256"] != digest:
        raise RemoteLocalImageError("local_images_transfer_failed")
    try:
        frame = json.loads(result["output_tail"].splitlines()[-1])
    except (ValueError, IndexError):
        raise RemoteLocalImageError("local_images_transfer_unknown") from None
    if frame != {"schema": 1, "state": "loaded", "bytes": size}:
        raise RemoteLocalImageError("local_images_transfer_unknown")
    return frame


_PUBLISH_PROGRAM = r'''import hashlib,json,os,stat,sys,tempfile
root,digest=sys.argv[1:]
if not root.startswith('/'): sys.exit(41)
raw=sys.stdin.buffer.read(262145)
if len(raw)>262144: sys.exit(41)
def closed(pairs):
 result={}
 for key,value in pairs:
  if key in result: raise ValueError('duplicate')
  result[key]=value
 return result
doc=json.loads(raw,object_pairs_hook=closed)
if 'sha256:'+hashlib.sha256(json.dumps({k:v for k,v in doc.items() if k!='remote_receipt_digest'},sort_keys=True,separators=(',',':')).encode()).hexdigest()!=digest: sys.exit(42)
os.makedirs(root,mode=0o700,exist_ok=True)
ri=os.lstat(root)
if not stat.S_ISDIR(ri.st_mode) or ri.st_uid!=os.geteuid() or stat.S_IMODE(ri.st_mode)&0o002: sys.exit(44)
os.chmod(root,0o700)
target=root+'/'+digest.split(':',1)[1]+'.json'
if os.path.lexists(target):
 ti=os.lstat(target)
 if not stat.S_ISREG(ti.st_mode) or ti.st_uid!=os.geteuid() or stat.S_IMODE(ti.st_mode)!=0o600 or ti.st_nlink!=1: sys.exit(45)
 with open(target,'rb') as h: old=h.read()
 if old!=raw: sys.exit(43)
else:
 fd,tmp=tempfile.mkstemp(prefix='.receipt-',dir=root)
 try:
  os.fchmod(fd,0o600);os.write(fd,raw);os.fsync(fd);os.close(fd);fd=-1;os.replace(tmp,target)
 finally:
  if fd>=0: os.close(fd)
  try: os.unlink(tmp)
  except OSError: pass
print(json.dumps({'schema':1,'remote_receipt_digest':digest},separators=(',',':')))
'''


def publish(remote_name: str, document: dict, *, timeout: int = 60,
            entry: dict | None = None) -> str:
    entry = _entry(remote_name) if entry is None else entry
    body = {key: value for key, value in document.items() if key != "remote_receipt_digest"}
    digest = local_images.canonical_digest(body)
    document = {**body, "remote_receipt_digest": digest}
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > local_images.MAX_RECEIPT_BYTES:
        raise RemoteLocalImageError("local_images_receipt_invalid")
    result = _remote.ssh_run(entry, shlex.join([
        "python3", "-c", _PUBLISH_PROGRAM, _receipt_root(entry), digest]),
        timeout=timeout, input_data=raw)
    if result.returncode != 0:
        raise RemoteLocalImageError("local_images_receipt_publish_unknown")
    output = result.stdout or ""
    try:
        # Byte stdin makes ssh_run capture byte stdout as well. Validate the
        # acknowledgement without replacing malformed UTF-8 or echoing it.
        if isinstance(output, bytes):
            if len(output) > local_images.MAX_CONFIG_BYTES:
                raise RemoteLocalImageError("local_images_receipt_publish_unknown")
            output = output.decode("utf-8", errors="strict")
        if len(output.encode("utf-8")) > local_images.MAX_CONFIG_BYTES:
            raise RemoteLocalImageError("local_images_receipt_publish_unknown")
        frame = json.loads(output)
    except (ValueError, UnicodeError):
        raise RemoteLocalImageError("local_images_receipt_publish_unknown") from None
    if frame != {"schema": 1, "remote_receipt_digest": digest}:
        raise RemoteLocalImageError("local_images_receipt_publish_unknown")
    return digest


_ENGINE_LOCK_PROGRAM = r'''import fcntl,json,os,stat,sys
root=sys.argv[1]
if not root.startswith('/'): sys.exit(61)
os.makedirs(root,mode=0o700,exist_ok=True)
ri=os.lstat(root)
if not stat.S_ISDIR(ri.st_mode) or ri.st_uid!=os.geteuid() or stat.S_IMODE(ri.st_mode)&0o002: sys.exit(62)
os.chmod(root,0o700)
# The worker fence survives loss of the engine lock's SSH owner. Test both
# without waiting; contention is a bounded busy refusal. Keep engine.lock held.
for name in ('engine.lock','load-fence.lock'):
 path=root+'/'+name
 fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 fi=os.fstat(fd)
 if not stat.S_ISREG(fi.st_mode) or fi.st_uid!=os.geteuid() or stat.S_IMODE(fi.st_mode)!=0o600 or fi.st_nlink!=1: sys.exit(63)
 try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:
  print(json.dumps({'schema':1,'acquired':False},separators=(',',':')),flush=True);sys.exit(75)
 if name=='engine.lock': engine_fd=fd
 else: fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
print(json.dumps({'schema':1,'acquired':True},separators=(',',':')),flush=True)
while os.read(0,4096): pass
'''


class _TransferLease:
    """Hold one remote-engine lock for the lifetime of the SSH connection."""

    def __init__(self, entry: dict, *, timeout: float = 30):
        self.entry = entry
        self.timeout = timeout
        self.process = None

    def __enter__(self):
        try:
            argv = ["python3", "-c", _ENGINE_LOCK_PROGRAM,
                    _local_image_root(self.entry)]
            self.process = subprocess.Popen(
                _remote.ssh_command_args(
                    self.entry, shlex.join(argv), multiplex=False),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            selector = selectors.DefaultSelector()
            selector.register(self.process.stdout, selectors.EVENT_READ)
            ready = selector.select(min(30, self.timeout))
            selector.close()
            if not ready:
                self._stop()
                raise RemoteLocalImageError("local_images_coordination_unavailable")
            line = self.process.stdout.readline(local_images.MAX_CONFIG_BYTES + 1)
            if len(line) > local_images.MAX_CONFIG_BYTES:
                self._stop()
                raise RemoteLocalImageError("local_images_coordination_unavailable")
            result = json.loads(line)
        except RemoteLocalImageError:
            raise
        except (OSError, ValueError, subprocess.SubprocessError):
            self._stop()
            raise RemoteLocalImageError("local_images_coordination_unavailable") from None
        if result == {"schema": 1, "acquired": False}:
            self._stop()
            raise RemoteLocalImageError("local_images_target_busy")
        if result != {"schema": 1, "acquired": True} or self.process.poll() is not None:
            self._stop()
            raise RemoteLocalImageError("local_images_coordination_unavailable")
        return self

    def lost(self) -> bool:
        return self.process is None or self.process.poll() is not None

    def require(self) -> None:
        if self.lost():
            raise RemoteLocalImageError("local_images_coordination_unknown")

    def _stop(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def __exit__(self, exc_type, _exc, _traceback):
        lost = self.lost()
        self._stop()
        if exc_type is None and lost:
            raise RemoteLocalImageError("local_images_coordination_unknown")
        return False


def target_image_lease(remote_name: str, project: str, environment: str,
                       receipt_digest: str, *, entry: dict | None = None,
                       timeout: float = 30) -> _TransferLease:
    """Return the remote-engine lock used by transfer and receipt-bound apply."""
    entry = _entry(remote_name) if entry is None else entry
    if not project or not environment or _DIGEST.fullmatch(receipt_digest) is None:
        raise RemoteLocalImageError("local_images_receipt_invalid")
    return _TransferLease(entry, timeout=timeout)


def _storage_admission(entry: dict, receipt: dict, observation: dict, *,
                       timeout: float = 60, before_write=None) -> None:
    """Reserve remote storage before the first transfer-side remote write."""
    local_images.validate_receipt(receipt)
    if entry.get("storage_floor_gb") in (None, 0):
        return
    from sandbox.resources.storage_floor import describe, remote_storage_floor_admission

    home = _remote.resolve_sandbox_home(entry)
    if not isinstance(home, str) or not home.startswith("/"):
        raise RemoteLocalImageError("local_images_remote_unavailable")
    base = posixpath.join(posixpath.normpath(home), "deploy-src")
    target = posixpath.normpath(posixpath.join(base, receipt["project"]))
    if posixpath.commonpath((base, target)) != base or target == base:
        raise RemoteLocalImageError("local_images_receipt_invalid")
    missing_bytes = 0
    for key, artifact in receipt["artifacts"].items():
        mismatches, _verified = local_images.compare_images(
            receipt, observation, artifacts={key})
        if mismatches:
            missing_bytes += artifact.get("archive_bytes", 0)
    if receipt["schema"] == local_images.RECEIPT_SCHEMA_V2:
        mismatches, _ = local_images.compare_images(receipt, observation)
        missing_bytes = sum(b["archive_bytes"] for b in receipt["bundles"].values()) if mismatches else 0
    # The upload file and Docker's content-store import can coexist.
    estimate = max(2 * missing_bytes, local_images.MAX_RECEIPT_BYTES)
    if before_write is not None:
        before_write()
    decision = remote_storage_floor_admission(
        entry, target, minimum_estimate_bytes=estimate, timeout=timeout)
    if decision is not None and decision.get("ok") is not True:
        raise RemoteLocalImageError("remote_storage_floor", describe(decision))


def push_receipt(remote_name: str, receipt_path_value: str | Path, *, timeout: int,
                 artifact_timeout: int, machine_identity: str,
                 registered_host_digest: str, progress=None,
                 entry: dict | None = None) -> dict:
    deadline = time.monotonic() + timeout
    wrote = False

    def mark_write():
        nonlocal wrote
        wrote = True

    def remaining(cap):
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise RemoteLocalImageError(
                "local_images_transfer_unknown" if wrote else "local_images_transfer_timeout")
        return min(cap, budget)

    receipt, path = local_images.load_receipt(receipt_path_value)
    entry = _entry(remote_name) if entry is None else entry
    try:
        observation = observe(remote_name, receipt, timeout=remaining(120), entry=entry)
        remaining(120)
        mismatches = local_images.compare_engine(receipt, observation)
        if mismatches:
            raise RemoteLocalImageError("local_images_platform_mismatch")
        budget = remaining(60)
        # Storage admission may create lock/reservation files even on refusal.
        _storage_admission(entry, receipt, observation, timeout=budget,
                           before_write=mark_write)
        budget = remaining(30)
        wrote = True  # The lease creates remote lock files.
        loaded, skipped = [], []
        with target_image_lease(
                remote_name, receipt["project"], receipt["environment"],
                receipt["receipt_digest"], entry=entry, timeout=budget) as lease:
            observation = observe(remote_name, receipt, timeout=remaining(120), entry=entry)
            remaining(30)
            lease.require()
            daemon_id_before = _checked(
                entry, "docker info --format '{{.ID}}'", timeout=remaining(30)).strip()
            remaining(30)
            if not daemon_id_before or len(daemon_id_before) > 256:
                raise RemoteLocalImageError("local_images_daemon_unavailable")
            transferred_bundles, transferred_bytes = [], 0
            if receipt["schema"] == local_images.RECEIPT_SCHEMA_V2:
                for key in sorted(receipt["artifacts"]):
                    mismatch, _ = local_images.compare_images(receipt, observation, artifacts={key})
                    (loaded if mismatch else skipped).append(key)
                if loaded:
                    for key, bundle in receipt["bundles"].items():
                        local_images.bundle_identities(path.parent / bundle["archive"], receipt["artifacts"])
                        load_archive(remote_name, path.parent / bundle["archive"], bundle,
                                     timeout=remaining(artifact_timeout), progress=progress,
                                     cancelled=lease.lost, entry=entry)
                        transferred_bundles.append(key)
                        transferred_bytes += bundle["archive_bytes"]
                    lease.require()
                    observation = observe(remote_name, receipt, timeout=remaining(120), entry=entry)
            else:
                for key, artifact in sorted(receipt["artifacts"].items()):
                    remaining(artifact_timeout)
                    mismatches, _ = local_images.compare_images(receipt, observation, artifacts={key})
                    if not mismatches:
                        skipped.append(key)
                        continue
                    load_archive(remote_name, path.parent / artifact["archive"], artifact,
                                 timeout=remaining(artifact_timeout), progress=progress,
                                 cancelled=lease.lost, entry=entry)
                    remaining(120)
                    lease.require()
                    observation = observe(remote_name, receipt, timeout=remaining(120), entry=entry)
                    remaining(120)
                    lease.require()
                    mismatches, _ = local_images.compare_images(receipt, observation, artifacts={key})
                    if mismatches:
                        raise RemoteLocalImageError("local_images_image_mismatch")
                    loaded.append(key)
                    transferred_bytes += artifact["archive_bytes"]
            mismatches, verified = local_images.compare_images(receipt, observation)
            if mismatches:
                raise RemoteLocalImageError("local_images_image_mismatch")
            engine_id = _checked(entry, "docker info --format '{{.ID}}'",
                                 timeout=remaining(30)).strip()
            remaining(60)
            lease.require()
            if not engine_id or len(engine_id) > 256 or engine_id != daemon_id_before:
                raise RemoteLocalImageError("local_images_daemon_unavailable")
            document = local_images.remote_receipt_document(
                receipt, verified, verified_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            document.update({"machine_identity": machine_identity,
                             "registered_host_digest": registered_host_digest,
                             "daemon_identity": engine_id})
            digest = publish(remote_name, document, timeout=remaining(60), entry=entry)
            remaining(60)
            lease.require()
    except (RemoteLocalImageError, subprocess.TimeoutExpired) as exc:
        # Reclassify timeouts using the aggregate clock, including a step that
        # returns late or an admission helper that converts timeout to refusal.
        remaining(1)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise RemoteLocalImageError(
                "local_images_transfer_unknown" if wrote else "local_images_transfer_timeout") from None
        raise
    return {"receipt": receipt, "remote_receipt_digest": digest,
            "loaded": loaded, "skipped": skipped, "daemon_identity": engine_id,
            "transferred_bundles": transferred_bundles, "transferred_bytes": transferred_bytes}
