"""Fixed registered-remote transport for secure image staging.

This is deliberately not a generic remote job transport.  It offers one fixed
helper verb and one bounded private input channel through the existing registered
remote seams.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import select
import secrets
import shlex
import subprocess
import time
from typing import Callable

from sandbox.hosting.images.staging_models import MAX_STAGE_FRAME_BYTES, canonical_bytes


_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
FIXED_HELPER_ENTRY = "sandbox-image-stage-helper-v1"
FIXED_HELPER_ENTRY_V2 = "sandbox-image-stage-helper-v2"
FIXED_HELPER_CHECK_ENTRY = "sandbox-image-stage-helper-check-v1"
READY_TIMEOUT_SECONDS = 15
MAX_BOOTSTRAP_FRAME_BYTES = 512
_BOOTSTRAP_PREFIX = b"BOOTSTRAP "
_BOOTSTRAP_CODES = {
    "inode": {"inode_os", "inode_json", "inode_key", "inode_exec"},
    "plan": {"plan_invalid"},
    "cgroup": {"cgroup_invalid"},
    "workspace": {"workspace_invalid"},
}
_UNIT_PROPERTIES = ("LoadState", "ActiveState", "SubState", "Description",
                    "MainPID", "ControlGroup", "Result", "ExecMainStatus")
_UNIT_SHOW = " ".join(f"--property={name}" for name in _UNIT_PROPERTIES)
_INODE_EXEC = r'''import hashlib,json,os,re,stat,sys
def fail(code):
 frames={'inode_os':b'BOOTSTRAP {"schema_version":1,"ok":false,"phase":"inode","code":"inode_os"}\n','inode_json':b'BOOTSTRAP {"schema_version":1,"ok":false,"phase":"inode","code":"inode_json"}\n','inode_key':b'BOOTSTRAP {"schema_version":1,"ok":false,"phase":"inode","code":"inode_key"}\n','inode_exec':b'BOOTSTRAP {"schema_version":1,"ok":false,"phase":"inode","code":"inode_exec"}\n'}
 try: os.write(1,frames[code])
 finally: os._exit(0)
root,home,expected,entry,manifest_name=sys.argv[1:]
if (entry,manifest_name) not in {('sandbox-image-stage-helper-v1','manifest.json'),('sandbox-image-stage-helper-v2','manifest-v2.json'),('sandbox-image-stage-helper-check-v1','manifest-v2.json')}: fail('inode_key')
if not home.startswith('/') or root.rsplit('/runtime/helpers/image-stage/',1)[0]!=home: fail('inode_key')
basename=root.rsplit('/',1)[-1]
if re.fullmatch(r'sha256-[0-9a-f]{64}-revision-[0-9a-f]{40}',basename) is None: fail('inode_key')
owner_uid=os.geteuid(); current=''; protected=home+'/runtime/helpers'
try:
 parts=root.split('/')[1:]; home_parts=home.split('/')[1:]
 if not parts or not home_parts or any(part in {'','.','..'} for part in parts+home_parts) or parts[:len(home_parts)]!=home_parts: fail('inode_key')
 current='/'+parts[0]
 dfd=os.open(current,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW); info=os.fstat(dfd); mode=stat.S_IMODE(info.st_mode)
 if not stat.S_ISDIR(info.st_mode) or mode&0o022: fail('inode_key')
 if (current==home and info.st_uid!=owner_uid) or (current!=home and info.st_uid not in {0,65534,owner_uid}): fail('inode_key')
 for part in parts[1:]:
  child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dfd); info=os.fstat(child)
  current+='/'+part; mode=stat.S_IMODE(info.st_mode)
  if not stat.S_ISDIR(info.st_mode): fail('inode_key')
  if current==home or current==home+'/runtime':
   if info.st_uid!=owner_uid or mode&0o022: fail('inode_key')
  elif current==protected or current.startswith(protected+'/'):
   if info.st_uid!=owner_uid or mode!=0o700: fail('inode_key')
  elif info.st_uid not in {0,owner_uid} or mode&0o022: fail('inode_key')
  os.close(dfd); dfd=child
except OSError: fail('inode_os')
def opened(name,mode):
 try: fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=dfd); info=os.fstat(fd)
 except OSError: fail('inode_os')
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or stat.S_IMODE(info.st_mode)!=mode or info.st_nlink!=1: fail('inode_key')
 return fd
hfd=opened('staging_helper.py',0o500); mfd=opened(manifest_name,0o600)
try:
 with os.fdopen(mfd,'rb') as mh: actual=json.loads(mh.read())
 wanted=json.loads(expected)
except (json.JSONDecodeError,UnicodeError): fail('inode_json')
try:
 if actual!=wanted: fail('inode_key')
 if basename!='sha256-'+actual['artifact_digest'].split(':',1)[1]+'-revision-'+actual['runtime_revision']: fail('inode_key')
except (KeyError,TypeError,AttributeError): fail('inode_key')
measured=hashlib.sha256()
try:
 while True:
  chunk=os.read(hfd,65536)
  if not chunk: break
  measured.update(chunk)
 if 'sha256:'+measured.hexdigest()!=actual['artifact_digest']: fail('inode_key')
 os.lseek(hfd,0,os.SEEK_SET); os.set_inheritable(hfd,True)
except OSError: fail('inode_os')
try: os.execv(sys.executable,[sys.executable,f'/proc/self/fd/{hfd}',entry])
except OSError: fail('inode_exec')'''


class RemoteImageStageError(RuntimeError):
    def __init__(self, code: str, *, process: dict | None = None,
                 cleanup: dict | None = None, bootstrap_phase: str | None = None,
                 bootstrap_code: str | None = None) -> None:
        self.code = code if code in {"remote_unavailable", "helper_failed", "protocol_invalid"} \
            else "helper_failed"
        self.process = process
        self.cleanup = cleanup
        self.bootstrap_phase = bootstrap_phase
        self.bootstrap_code = bootstrap_code
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class RemoteStageResponse:
    ok: bool
    code: str
    payload: dict
    schema_version: int = 1


def parse_stage_response(value: object) -> RemoteStageResponse:
    if type(value) is not dict or set(value) != {"schema_version", "ok", "code", "payload"} \
            or type(value["schema_version"]) is not int \
            or value["schema_version"] not in {1, 2} or type(value["ok"]) is not bool \
            or type(value["code"]) is not str or type(value["payload"]) is not dict:
        raise RemoteImageStageError("protocol_invalid")
    canonical_bytes(value)
    return RemoteStageResponse(value["ok"], value["code"], value["payload"],
                               value["schema_version"])


def parse_bootstrap_line(value: object) -> dict | None:
    """Parse READY or one closed, bounded, secret-free pre-READY failure."""
    if type(value) is not bytes or not 1 <= len(value) <= MAX_BOOTSTRAP_FRAME_BYTES \
            or not value.endswith(b"\n") or value.count(b"\n") != 1:
        raise RemoteImageStageError(
            "helper_failed", bootstrap_phase="unknown",
            bootstrap_code="bootstrap_unavailable")
    if value == b"READY\n":
        return None
    if not value.startswith(_BOOTSTRAP_PREFIX) or not value.isascii():
        raise RemoteImageStageError(
            "helper_failed", bootstrap_phase="unknown",
            bootstrap_code="bootstrap_unavailable")
    try:
        frame = json.loads(value[len(_BOOTSTRAP_PREFIX):-1].decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise RemoteImageStageError(
            "helper_failed", bootstrap_phase="unknown",
            bootstrap_code="bootstrap_unavailable") from None
    if type(frame) is not dict or set(frame) != {
            "schema_version", "ok", "phase", "code"} \
            or frame["schema_version"] != 1 or frame["ok"] is not False \
            or frame.get("phase") not in _BOOTSTRAP_CODES \
            or frame.get("code") not in _BOOTSTRAP_CODES[frame["phase"]]:
        raise RemoteImageStageError(
            "helper_failed", bootstrap_phase="unknown",
            bootstrap_code="bootstrap_unavailable")
    return frame


class RegisteredRemoteImageTransport:
    """One fixed transport over ``sandbox.core._remote`` injection seams."""

    def __init__(self, *, remote_lookup: Callable | None = None,
                 ssh_private_frame: Callable | None = None,
                 unit_observer: Callable | None = None,
                 resolve_home: Callable | None = None) -> None:
        if remote_lookup is None or ssh_private_frame is None \
                or unit_observer is None or resolve_home is None:
            from sandbox.core import _remote
            remote_lookup = remote_lookup or _remote.get_remote
            if ssh_private_frame is None:
                def ssh_private_frame(remote, argv, frame, **kwargs):
                    command = shlex.join(argv)
                    return _remote.ssh_run(
                        remote, command, timeout=kwargs["timeout"], input_data=frame)
            unit_observer = unit_observer or _remote.ssh_run
            resolve_home = resolve_home or _remote.resolve_sandbox_home
        self._lookup = remote_lookup
        self._send = ssh_private_frame
        self._observe_unit = unit_observer
        self._resolve_home = resolve_home

    def observe_authority(self, remote_name: str, helper: object) -> dict:
        """Return one closed daemon/helper projection without starting staging."""
        if type(remote_name) is not str or _REMOTE.fullmatch(remote_name) is None:
            raise RemoteImageStageError("remote_unavailable")
        remote = self._lookup(remote_name)
        if type(remote) is not dict or remote.get("provisioned") is not True:
            raise RemoteImageStageError("remote_unavailable")
        raw = helper.as_mapping() if callable(getattr(helper, "as_mapping", None)) else None
        if type(raw) is not dict or set(raw) != {"artifact_digest", "entry",
                "runtime_revision", "capability_revision"} \
                or raw["entry"] != FIXED_HELPER_ENTRY_V2 \
                or raw["capability_revision"] != "systemd-cgroup-v2-batch-stage-v2" \
                or re.fullmatch(r"sha256:[0-9a-f]{64}", raw["artifact_digest"] or "") is None \
                or re.fullmatch(r"[0-9a-f]{40}", raw["runtime_revision"] or "") is None:
            raise RemoteImageStageError("protocol_invalid")
        home = self._resolve_home(remote)
        if type(home) is not str or not home.startswith("/"):
            raise RemoteImageStageError("remote_unavailable")
        digest = raw["artifact_digest"].split(":", 1)[1]
        root = (f"{home}/runtime/helpers/image-stage/sha256-{digest}"
                f"-revision-{raw['runtime_revision']}")
        measured = self._observe_unit(remote,
            "sha256sum -- " + shlex.quote(f"{root}/staging_helper.py"), timeout=15)
        manifest = self._observe_unit(remote,
            "test -f " + shlex.quote(f"{root}/manifest-v2.json") + " && cat -- "
            + shlex.quote(f"{root}/manifest-v2.json"), timeout=15)
        daemon = self._observe_unit(remote,
            "docker info --format '{{.ID}}'", timeout=15)
        try:
            manifest_value = json.loads(str(getattr(manifest, "stdout", "")))
        except json.JSONDecodeError:
            raise RemoteImageStageError("helper_failed") from None
        daemon_value = str(getattr(daemon, "stdout", "")).strip()
        if (getattr(measured, "returncode", 1) != 0
                or str(getattr(measured, "stdout", "")).split(maxsplit=1)[0] != digest
                or getattr(manifest, "returncode", 1) != 0
                or manifest_value != {"schema_version": 2, **raw}
                or getattr(daemon, "returncode", 1) != 0
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", daemon_value) is None):
            raise RemoteImageStageError("helper_failed")
        return {"daemon_identity": daemon_value, "helper": raw}

    def helper_check(self, remote_name: str, helper: object, *, timeout_seconds: int = 30) -> dict:
        """Run only the measured wrapper, user-unit, cgroup, and workspace gates."""
        if type(remote_name) is not str or _REMOTE.fullmatch(remote_name) is None:
            raise RemoteImageStageError("remote_unavailable")
        remote = self._lookup(remote_name)
        raw = helper.as_mapping() if callable(getattr(helper, "as_mapping", None)) else None
        if type(remote) is not dict or remote.get("provisioned") is not True:
            raise RemoteImageStageError("remote_unavailable")
        if type(raw) is not dict or set(raw) != {"artifact_digest", "entry",
                "runtime_revision", "capability_revision"} \
                or raw["entry"] != FIXED_HELPER_ENTRY_V2 \
                or raw["capability_revision"] != "systemd-cgroup-v2-batch-stage-v2" \
                or re.fullmatch(r"sha256:[0-9a-f]{64}", str(raw["artifact_digest"])) is None \
                or re.fullmatch(r"[0-9a-f]{40}", str(raw["runtime_revision"])) is None:
            raise RemoteImageStageError("protocol_invalid")
        home = self._resolve_home(remote)
        if type(home) is not str or not home.startswith("/"):
            raise RemoteImageStageError("remote_unavailable")
        digest = raw["artifact_digest"].split(":", 1)[1]
        root = f"{home}/runtime/helpers/image-stage/sha256-{digest}-revision-{raw['runtime_revision']}"
        expected_manifest = {"schema_version": 2, **raw}
        measured = self._observe_unit(
            remote, "sha256sum -- " + shlex.quote(f"{root}/staging_helper.py"), timeout=15)
        manifest_result = self._observe_unit(
            remote, "test -f " + shlex.quote(f"{root}/manifest-v2.json") + " && cat -- "
            + shlex.quote(f"{root}/manifest-v2.json"), timeout=15)
        uid_result = self._observe_unit(remote, "id -u", timeout=15)
        try:
            manifest = json.loads(str(getattr(manifest_result, "stdout", "")))
            uid_text = str(getattr(uid_result, "stdout", "")).strip()
        except (UnicodeError, json.JSONDecodeError):
            raise RemoteImageStageError("helper_failed") from None
        if getattr(measured, "returncode", 1) != 0 \
                or str(getattr(measured, "stdout", "")).split(maxsplit=1)[0] != digest \
                or getattr(manifest_result, "returncode", 1) != 0 \
                or manifest != expected_manifest or getattr(uid_result, "returncode", 1) != 0 \
                or not uid_text.isascii() or not uid_text.isdecimal() \
                or not 1 <= int(uid_text) <= 2**31 - 1:
            raise RemoteImageStageError("helper_failed")
        uid = int(uid_text)
        identity = secrets.token_hex(16)
        unit = f"sandbox-image-stage-check-{identity}.service"
        description = f"sandbox-image-stage-check-attempt-{identity}"
        argv = ("systemd-run", "--user", f"--unit={unit}", f"--description={description}",
            "--quiet", "--pipe", "--property=KillMode=control-group", "--property=Delegate=no",
            "--property=NoNewPrivileges=yes", "--property=RestrictSUIDSGID=yes",
            "--property=ProtectControlGroups=yes", "--", "python3", "-c", _INODE_EXEC,
            root, home, json.dumps(expected_manifest, sort_keys=True, separators=(",", ":")),
            FIXED_HELPER_CHECK_ENTRY, "manifest-v2.json")
        if hasattr(self._send, "prepare"):
            process = self._send.prepare(remote, argv, timeout=timeout_seconds)
        else:
            from sandbox.core import _remote
            process = subprocess.Popen(
                _remote.ssh_command_args(remote, shlex.join(argv)), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"PATH": "/usr/bin:/bin"},
                shell=False, start_new_session=True)
        bootstrap_phase = bootstrap_code = None
        try:
            failure = parse_bootstrap_line(self._read_bootstrap(process))
            if failure is not None:
                bootstrap_phase, bootstrap_code = failure["phase"], failure["code"]
                raise RemoteImageStageError("helper_failed")
            cgroup = self._observe_launch_authority(remote, unit, description, uid)
            process.stdin.write(b"CHECK\n"); process.stdin.close(); process.stdin = None
            output, _stderr = process.communicate(timeout=timeout_seconds)
            if process.returncode != 74 or output != b"CHECKED\n":
                raise RemoteImageStageError("helper_failed")
        except Exception as exc:
            bootstrap_phase = bootstrap_phase or getattr(exc, "bootstrap_phase", None)
            bootstrap_code = bootstrap_code or getattr(exc, "bootstrap_code", None)
            try: process.kill()
            except ProcessLookupError: pass
            process_evidence, cleanup = self._cleanup_failed_launch(
                remote, unit, description, uid)
            if bootstrap_code is not None:
                process_evidence = {**process_evidence,
                    "bootstrap_phase": bootstrap_phase, "bootstrap_code": bootstrap_code}
            raise RemoteImageStageError(
                "helper_failed", process=process_evidence, cleanup=cleanup,
                bootstrap_phase=bootstrap_phase, bootstrap_code=bootstrap_code) from None
        process_evidence, cleanup = self._cleanup_failed_launch(remote, unit, description, uid)
        if cleanup != {"complete": True}:
            raise RemoteImageStageError(
                "helper_failed", process=process_evidence, cleanup=cleanup)
        return {"ok": True, "code": "ready", "process": process_evidence,
                "cleanup": cleanup, "cgroup": cgroup}

    def prepare(self, remote_name: str, plan_frame: dict, *, timeout_seconds: int):
        if type(remote_name) is not str or _REMOTE.fullmatch(remote_name) is None:
            raise RemoteImageStageError("remote_unavailable")
        remote = self._lookup(remote_name)
        if type(remote) is not dict or remote.get("provisioned") is not True:
            raise RemoteImageStageError("remote_unavailable")
        public_frame = canonical_bytes(plan_frame)
        # Length-prefix the separate frames.  Credential bytes never enter argv,
        # environment, logs, or the public frame.
        unit = plan_frame.get("unit_name") if type(plan_frame) is dict else None
        if type(unit) is not str or re.fullmatch(r"sandbox-image-stage-[0-9a-f]{32}\.service", unit) is None:
            raise RemoteImageStageError("protocol_invalid")
        helper = plan_frame.get("helper")
        if type(helper) is not dict or set(helper) != {"artifact_digest", "entry",
                "runtime_revision", "capability_revision"} \
                or helper["entry"] not in {FIXED_HELPER_ENTRY, FIXED_HELPER_ENTRY_V2} \
                or re.fullmatch(r"sha256:[0-9a-f]{64}", str(helper["artifact_digest"])) is None \
                or re.fullmatch(r"[0-9a-f]{40}", str(helper["runtime_revision"])) is None:
            raise RemoteImageStageError("protocol_invalid")
        if type(plan_frame.get("schema_version")) is not int \
                or (plan_frame.get("schema_version"), helper["entry"]) not in {
                (1, FIXED_HELPER_ENTRY), (2, FIXED_HELPER_ENTRY_V2)}:
            raise RemoteImageStageError("protocol_invalid")
        if plan_frame["schema_version"] == 2 \
                and (helper["capability_revision"] != "systemd-cgroup-v2-batch-stage-v2"
                     or type(helper["runtime_revision"]) is not str
                     or re.fullmatch(r"[0-9a-f]{40}", helper["runtime_revision"]) is None):
            raise RemoteImageStageError("protocol_invalid")
        home = self._resolve_home(remote)
        if type(home) is not str or not home.startswith("/"):
            raise RemoteImageStageError("remote_unavailable")
        digest_hex = helper["artifact_digest"].split(":", 1)[1]
        helper_root = (f"{home}/runtime/helpers/image-stage/sha256-{digest_hex}"
                       f"-revision-{helper['runtime_revision']}")
        helper_path = f"{helper_root}/staging_helper.py"
        manifest_name = "manifest-v2.json" if plan_frame["schema_version"] == 2 \
            else "manifest.json"
        manifest_path = f"{helper_root}/{manifest_name}"
        measured = self._observe_unit(
            remote, "sha256sum -- " + shlex.quote(helper_path), timeout=15)
        if getattr(measured, "returncode", 1) != 0 \
                or str(getattr(measured, "stdout", "")).split(maxsplit=1)[0] != digest_hex:
            raise RemoteImageStageError("helper_failed")
        manifest_result = self._observe_unit(
            remote, "test -f " + shlex.quote(manifest_path) + " && cat -- "
            + shlex.quote(manifest_path), timeout=15)
        try:
            manifest = json.loads(str(getattr(manifest_result, "stdout", "")))
        except json.JSONDecodeError:
            raise RemoteImageStageError("helper_failed") from None
        expected_manifest = {"schema_version": plan_frame["schema_version"], **helper}
        if getattr(manifest_result, "returncode", 1) != 0 or manifest != expected_manifest:
            raise RemoteImageStageError("helper_failed")
        uid_result = self._observe_unit(remote, "id -u", timeout=15)
        uid_text = str(getattr(uid_result, "stdout", "")).strip()
        if getattr(uid_result, "returncode", 1) != 0 or not uid_text.isascii() \
                or not uid_text.isdecimal() or not 1 <= int(uid_text) <= 2**31 - 1:
            raise RemoteImageStageError("helper_failed")
        service_uid = int(uid_text)
        attempt_id = secrets.token_hex(16)
        description = f"sandbox-image-stage-attempt-{attempt_id}"
        argv = ("systemd-run", "--user", f"--unit={unit}", f"--description={description}",
             "--quiet", "--pipe",
             "--property=KillMode=control-group", "--property=Delegate=no",
             "--property=NoNewPrivileges=yes", "--property=RestrictSUIDSGID=yes",
             "--property=ProtectControlGroups=yes", "--",
             "python3", "-c", _INODE_EXEC, helper_root, home,
             json.dumps(expected_manifest, sort_keys=True, separators=(",", ":")),
             helper["entry"], manifest_name)
        # The default seam deliberately uses the registered remote's exact SSH
        # argument constructor; fakes may inject a prepared channel instead.
        if hasattr(self._send, "prepare"):
            process = self._send.prepare(remote, argv, timeout=timeout_seconds)
        else:
            from sandbox.core import _remote
            process = subprocess.Popen(
                _remote.ssh_command_args(remote, shlex.join(argv)), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"PATH": "/usr/bin:/bin"},
                shell=False, start_new_session=True)
        try:
            process.stdin.write(len(public_frame).to_bytes(4, "big") + public_frame)
            process.stdin.flush()
            ready = self._read_bootstrap(process)
            failure = parse_bootstrap_line(ready)
            if failure is not None:
                raise RemoteImageStageError(
                    "helper_failed", bootstrap_phase=failure["phase"],
                    bootstrap_code=failure["code"])
            launch_cgroup = self._observe_launch_authority(
                remote, unit, description, service_uid)
        except Exception as exc:
            try: process.kill()
            except ProcessLookupError: pass
            process_evidence, cleanup_evidence = self._cleanup_failed_launch(
                remote, unit, description, service_uid)
            bootstrap_phase = getattr(exc, "bootstrap_phase", None)
            bootstrap_code = getattr(exc, "bootstrap_code", None)
            if bootstrap_code is not None:
                process_evidence = {**process_evidence,
                    "bootstrap_phase": bootstrap_phase,
                    "bootstrap_code": bootstrap_code}
            raise RemoteImageStageError(
                "helper_failed", process=process_evidence, cleanup=cleanup_evidence,
                bootstrap_phase=bootstrap_phase, bootstrap_code=bootstrap_code) from None
        return _PreparedRemoteStage(process, remote, unit, description, launch_cgroup,
                                    self._observe_unit, timeout_seconds)

    @staticmethod
    def _read_bootstrap(process) -> bytes:
        if hasattr(process, "read_ready"):
            value = process.read_ready(READY_TIMEOUT_SECONDS)
            return value if type(value) is bytes else b""
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        output = bytearray()
        descriptor = process.stdout.fileno()
        while len(output) <= MAX_BOOTSTRAP_FRAME_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if not readable:
                break
            chunk = os.read(descriptor, min(128, MAX_BOOTSTRAP_FRAME_BYTES + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if b"\n" in chunk:
                break
        return bytes(output)

    def _observe_launch_authority(self, remote, unit: str, description: str, uid: int) -> str:
        result = self._observe_unit(
            remote, "systemctl --user show --property=ActiveState --property=Description "
            "--property=ControlGroup --property=KillMode --property=Delegate "
            "--property=NoNewPrivileges --property=RestrictSUIDSGID "
            "--property=ProtectControlGroups " + shlex.quote(unit), timeout=15)
        values = {}
        for line in str(getattr(result, "stdout", "")).splitlines():
            key, separator, value = line.partition("=")
            if separator and key not in values:
                values[key] = value
        expected_cgroup = (f"/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/"
                           f"{unit}")
        expected = {"ActiveState": "active", "Description": description,
                    "ControlGroup": expected_cgroup, "KillMode": "control-group",
                    "Delegate": "no", "NoNewPrivileges": "yes",
                    "RestrictSUIDSGID": "yes", "ProtectControlGroups": "yes"}
        if getattr(result, "returncode", 1) != 0 or values != expected:
            raise RemoteImageStageError("helper_failed")
        return expected_cgroup

    def _kill_unit(self, remote, unit: str, description: str) -> None:
        if not self._unit_attempt_matches(remote, unit, description):
            return
        self._observe_unit(remote, "systemctl --user kill --kill-whom=all --signal=SIGKILL "
                           + shlex.quote(unit), timeout=15)
        self._observe_unit(remote, "systemctl --user stop " + shlex.quote(unit), timeout=15)

    def _cleanup_failed_launch(self, remote, unit: str, description: str,
                               uid: int) -> tuple[dict, dict]:
        expected_cgroup = (f"/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/"
                           f"{unit}")
        command = "systemctl --user show " + _UNIT_SHOW + " " + shlex.quote(unit)
        observed = self._observe_unit(remote, command, timeout=15)
        values = self._closed_unit_properties(observed)
        if values is None:
            return ({"unit_inactive": False, "cgroup_empty_or_removed": False},
                    {"complete": False})
        initially_absent = self._not_found_unit(values, unit)
        if not initially_absent and values["Description"] != description:
            return ({"unit_inactive": False, "cgroup_empty_or_removed": False},
                    {"complete": False})
        active_owned = (values["LoadState"] == "loaded"
            and values["ActiveState"] == "active" and values["SubState"] == "running"
            and values["MainPID"].isascii() and values["MainPID"].isdecimal()
            and int(values["MainPID"]) > 0 and values["ControlGroup"] == expected_cgroup
            and values["Result"] == "success" and values["ExecMainStatus"] == "0")
        if active_owned:
            # Action return codes are not cleanup authority. Only the closed
            # terminal observation and exact cgroup proof below decide safety.
            self._observe_unit(
                remote, "systemctl --user kill --kill-whom=all --signal=SIGKILL "
                + shlex.quote(unit), timeout=15)
            self._observe_unit(
                remote, "systemctl --user stop " + shlex.quote(unit), timeout=15)
            terminal = self._observe_unit(remote, command, timeout=15)
            values = self._closed_unit_properties(terminal)
        terminal_safe = initially_absent or self._terminal_attempt(values, description)
        empty = self._observe_unit(
            remote, "test ! -e " + shlex.quote("/sys/fs/cgroup" + expected_cgroup)
            + " || grep -qx 'populated 0' "
            + shlex.quote("/sys/fs/cgroup" + expected_cgroup + "/cgroup.events"), timeout=15)
        safe = terminal_safe and getattr(empty, "returncode", 1) == 0
        if initially_absent:
            return ({"unit_inactive": safe, "cgroup_empty_or_removed": safe,
                     "not_launched": safe}, {"complete": safe})
        if not safe:
            return ({"unit_inactive": False, "cgroup_empty_or_removed": False},
                    {"complete": False})
        reset = self._observe_unit(
            remote, "systemctl --user reset-failed " + shlex.quote(unit), timeout=15)
        after_reset = self._closed_unit_properties(
            self._observe_unit(remote, command, timeout=15))
        empty_after_reset = self._observe_unit(
            remote, "test ! -e " + shlex.quote("/sys/fs/cgroup" + expected_cgroup)
            + " || grep -qx 'populated 0' "
            + shlex.quote("/sys/fs/cgroup" + expected_cgroup + "/cgroup.events"), timeout=15)
        complete = (getattr(reset, "returncode", 1) == 0
                    and self._not_found_unit(after_reset, unit)
                    and getattr(empty_after_reset, "returncode", 1) == 0)
        return ({"unit_inactive": complete, "cgroup_empty_or_removed": complete},
                {"complete": complete})

    @staticmethod
    def _closed_unit_properties(result) -> dict[str, str] | None:
        if getattr(result, "returncode", 1) != 0:
            return None
        values: dict[str, str] = {}
        for line in str(getattr(result, "stdout", "")).splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in _UNIT_PROPERTIES or key in values:
                return None
            values[key] = value
        return values if set(values) == set(_UNIT_PROPERTIES) else None

    @staticmethod
    def _terminal_attempt(values: dict[str, str] | None, description: str) -> bool:
        if values is None or values["LoadState"] != "loaded" \
                or values["Description"] != description or values["MainPID"] != "0" \
                or values["ControlGroup"] != "":
            return False
        inactive = (values["ActiveState"], values["SubState"], values["Result"],
                    values["ExecMainStatus"]) == ("inactive", "dead", "success", "0")
        failed_status = values["ExecMainStatus"]
        failed = ((values["ActiveState"], values["SubState"], values["Result"])
                  == ("failed", "failed", "exit-code")
                  and failed_status.isascii() and failed_status.isdecimal()
                  and 1 <= int(failed_status) <= 255)
        return inactive or failed

    @staticmethod
    def _not_found_unit(values: dict[str, str] | None, unit: str) -> bool:
        return values is not None \
            and values["LoadState"] == "not-found" \
            and values["ActiveState"] == "inactive" \
            and values["SubState"] == "dead" \
            and values["Description"] in {"", unit} \
            and values["MainPID"] == "0" \
            and values["ControlGroup"] == "" \
            and values["Result"] == "success" \
            and values["ExecMainStatus"] == "0"

    def _unit_attempt_matches(self, remote, unit: str, description: str) -> bool:
        result = self._observe_unit(
            remote, "systemctl --user show --property=Description --value "
            + shlex.quote(unit), timeout=15)
        return (getattr(result, "returncode", 1) == 0
                and str(getattr(result, "stdout", "")).strip() == description)

    def invoke(self, remote_name: str, plan_frame: dict, credential: bytes,
               *, timeout_seconds: int) -> RemoteStageResponse:
        return self.prepare(remote_name, plan_frame, timeout_seconds=timeout_seconds).deliver(credential)


class _PreparedRemoteStage:
    def __init__(self, process, remote, unit, attempt_description, launch_cgroup,
                 observer, timeout):
        self.process = process; self.remote = remote; self.unit = unit
        self.attempt_description = attempt_description; self.launch_cgroup = launch_cgroup
        self.observer = observer; self.timeout = timeout; self.used = False

    def deliver(self, credential: bytes) -> RemoteStageResponse:
        if self.used or type(credential) is not bytes or not credential or len(credential) > 64 * 1024:
            raise RemoteImageStageError("protocol_invalid")
        self.used = True
        try:
            self.process.stdin.write(len(credential).to_bytes(4, "big") + credential)
            self.process.stdin.close(); self.process.stdin = None
            output, _stderr = self.process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._kill_whole_unit()
            raise RemoteImageStageError("helper_failed") from None
        if self.process.returncode != 0 or len(output) > MAX_STAGE_FRAME_BYTES:
            raise RemoteImageStageError("helper_failed")
        try:
            response = parse_stage_response(json.loads(output.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError, RemoteImageStageError):
            raise RemoteImageStageError("protocol_invalid") from None
        # Without RemainAfterExit, systemd-run --pipe returns only after the
        # helper exits and the transient unit may already be unloaded.  Launch
        # ownership was proven before the credential crossed the boundary.
        observed = self.observer(
            self.remote,
            "systemctl --user show --property=LoadState --property=ActiveState --property=Description "
            "--property=ControlGroup "
            + shlex.quote(self.unit), timeout=15,
        )
        values = self._properties(observed)
        if getattr(observed, "returncode", 1) != 0:
            raise RemoteImageStageError("helper_failed")
        load_state = values.get("LoadState")
        if load_state == "not-found":
            terminal_unit = (values.get("ActiveState") == "inactive"
                and values.get("Description", "") in {"", self.unit}
                and values.get("ControlGroup", "") == "")
        else:
            terminal_unit = (load_state == "loaded"
                and values.get("ActiveState") == "inactive"
                and values.get("Description") == self.attempt_description
                and values.get("ControlGroup", "") in {"", self.launch_cgroup})
        if not terminal_unit:
            raise RemoteImageStageError("helper_failed")
        process = response.payload.get("process")
        reported_cgroup = process.get("cgroup") if type(process) is dict else None
        if type(reported_cgroup) is not str or reported_cgroup != self.launch_cgroup:
            raise RemoteImageStageError("helper_failed")
        populated = self.observer(
            self.remote, "test ! -e " + shlex.quote("/sys/fs/cgroup" + reported_cgroup)
            + " || grep -qx 'populated 0' "
            + shlex.quote("/sys/fs/cgroup" + reported_cgroup + "/cgroup.events"), timeout=15)
        if getattr(populated, "returncode", 1) != 0:
            raise RemoteImageStageError("helper_failed")
        if type(response.payload.get("process")) is dict:
            response.payload["process"]["unit_inactive"] = True
            response.payload["process"]["cgroup_empty_or_removed"] = True
        if load_state == "loaded":
            collected = self.observer(
                self.remote, "systemctl --user reset-failed " + shlex.quote(self.unit), timeout=15)
            if getattr(collected, "returncode", 1) != 0:
                raise RemoteImageStageError("helper_failed")
        return response

    def _kill_whole_unit(self) -> None:
        if not self._owns_unit():
            return
        self.observer(self.remote, "systemctl --user kill --kill-whom=all --signal=SIGKILL "
                      + shlex.quote(self.unit), timeout=15)
        if self.process.poll() is None:
            try: self.process.kill()
            except ProcessLookupError: pass

    def cancel(self) -> dict:
        identity_result = self.observer(
            self.remote, "systemctl --user show --property=Description --property=ControlGroup "
            + shlex.quote(self.unit), timeout=15)
        identity = self._properties(identity_result)
        if identity.get("Description") != self.attempt_description:
            return {"unit_inactive": False, "cgroup_empty_or_removed": False,
                    "cleanup_complete": not self.used}
        cgroup = identity.get("ControlGroup", "")
        self._kill_whole_unit()
        stopped = self.observer(
            self.remote, "systemctl --user stop " + shlex.quote(self.unit), timeout=15)
        if getattr(stopped, "returncode", 1) != 0:
            return {"unit_inactive": False, "cgroup_empty_or_removed": False,
                    "cleanup_complete": not self.used}
        observed = self.observer(
            self.remote, "systemctl --user show --property=ActiveState --property=Description "
            + shlex.quote(self.unit), timeout=15)
        stopped_values = self._properties(observed)
        inactive = (stopped_values.get("ActiveState") == "inactive"
                    and stopped_values.get("Description") == self.attempt_description)
        empty = False
        if cgroup == self.launch_cgroup:
            checked = self.observer(
                self.remote, "test ! -e " + shlex.quote("/sys/fs/cgroup" + cgroup)
                + " || grep -qx 'populated 0' "
                + shlex.quote("/sys/fs/cgroup" + cgroup + "/cgroup.events"), timeout=15)
            empty = getattr(checked, "returncode", 1) == 0
        return {"unit_inactive": inactive, "cgroup_empty_or_removed": empty,
                "cleanup_complete": not self.used}

    def _owns_unit(self) -> bool:
        result = self.observer(
            self.remote, "systemctl --user show --property=Description --value "
            + shlex.quote(self.unit), timeout=15)
        return (getattr(result, "returncode", 1) == 0
                and str(getattr(result, "stdout", "")).strip()
                == self.attempt_description)

    @staticmethod
    def _properties(result) -> dict[str, str]:
        if getattr(result, "returncode", 1) != 0:
            return {}
        values = {}
        for line in str(getattr(result, "stdout", "")).splitlines():
            key, separator, value = line.partition("=")
            if separator and key not in values:
                values[key] = value
        return values
