"""Fixed registered-remote transport for secure image staging.

This is deliberately not a generic remote job transport.  It offers one fixed
helper verb and one bounded private input channel through the existing registered
remote seams.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import select
import shlex
import subprocess
from typing import Callable

from sandbox.hosting.images.staging_models import MAX_STAGE_FRAME_BYTES, canonical_bytes


_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
FIXED_HELPER_ENTRY = "sandbox-image-stage-helper-v1"
READY_TIMEOUT_SECONDS = 15
_INODE_EXEC = r'''import hashlib,json,os,stat,sys
root,expected,entry=sys.argv[1:]
dfd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
for part in root.split('/')[1:]:
 child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dfd); info=os.fstat(child)
 if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)&0o022: raise SystemExit(69)
 os.close(dfd); dfd=child
def opened(name,mode):
 fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=dfd); info=os.fstat(fd)
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=mode or info.st_nlink!=1: raise SystemExit(70)
 return fd
hfd=opened('staging_helper.py',0o500); mfd=opened('manifest.json',0o600)
with os.fdopen(mfd,'rb') as mh: actual=json.loads(mh.read())
if actual!=json.loads(expected): raise SystemExit(71)
measured=hashlib.sha256()
while True:
 chunk=os.read(hfd,65536)
 if not chunk: break
 measured.update(chunk)
if 'sha256:'+measured.hexdigest()!=actual['artifact_digest']: raise SystemExit(72)
os.lseek(hfd,0,os.SEEK_SET); os.set_inheritable(hfd,True)
os.execv(sys.executable,[sys.executable,f'/proc/self/fd/{hfd}',entry])'''


class RemoteImageStageError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in {"remote_unavailable", "helper_failed", "protocol_invalid"} \
            else "helper_failed"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class RemoteStageResponse:
    ok: bool
    code: str
    payload: dict


def parse_stage_response(value: object) -> RemoteStageResponse:
    if type(value) is not dict or set(value) != {"schema_version", "ok", "code", "payload"} \
            or value["schema_version"] != 1 or type(value["ok"]) is not bool \
            or type(value["code"]) is not str or type(value["payload"]) is not dict:
        raise RemoteImageStageError("protocol_invalid")
    canonical_bytes(value)
    return RemoteStageResponse(value["ok"], value["code"], value["payload"])


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
                or helper["entry"] != FIXED_HELPER_ENTRY \
                or re.fullmatch(r"sha256:[0-9a-f]{64}", str(helper["artifact_digest"])) is None:
            raise RemoteImageStageError("protocol_invalid")
        home = self._resolve_home(remote)
        if type(home) is not str or not home.startswith("/"):
            raise RemoteImageStageError("remote_unavailable")
        digest_hex = helper["artifact_digest"].split(":", 1)[1]
        helper_root = f"{home}/runtime/helpers/image-stage/sha256-{digest_hex}"
        helper_path = f"{helper_root}/staging_helper.py"
        manifest_path = f"{helper_root}/manifest.json"
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
        expected_manifest = {"schema_version": 1, **helper}
        if getattr(manifest_result, "returncode", 1) != 0 or manifest != expected_manifest:
            raise RemoteImageStageError("helper_failed")
        argv = ("systemd-run", f"--unit={unit}", "--quiet", "--pipe",
             "--property=KillMode=control-group", "--property=Delegate=no",
             "--property=RemainAfterExit=yes",
             "--property=NoNewPrivileges=yes", "--property=RestrictSUIDSGID=yes",
             "--property=ProtectControlGroups=yes", "--",
             "python3", "-c", _INODE_EXEC, helper_root,
             json.dumps(expected_manifest, sort_keys=True, separators=(",", ":")),
             FIXED_HELPER_ENTRY)
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
            if hasattr(process, "read_ready"):
                ready = process.read_ready(READY_TIMEOUT_SECONDS)
            else:
                readable, _, _ = select.select(
                    [process.stdout], [], [], READY_TIMEOUT_SECONDS)
                ready = process.stdout.read(6) if readable else b""
            if ready != b"READY\n":
                self._kill_unit(remote, unit)
                raise RemoteImageStageError("helper_failed")
        except Exception:
            self._kill_unit(remote, unit)
            process.kill()
            raise RemoteImageStageError("helper_failed") from None
        return _PreparedRemoteStage(process, remote, unit, self._observe_unit, timeout_seconds)

    def _kill_unit(self, remote, unit: str) -> None:
        self._observe_unit(remote, "systemctl kill --kill-whom=all --signal=SIGKILL "
                           + shlex.quote(unit), timeout=15)
        self._observe_unit(remote, "systemctl stop " + shlex.quote(unit), timeout=15)

    def invoke(self, remote_name: str, plan_frame: dict, credential: bytes,
               *, timeout_seconds: int) -> RemoteStageResponse:
        return self.prepare(remote_name, plan_frame, timeout_seconds=timeout_seconds).deliver(credential)


class _PreparedRemoteStage:
    def __init__(self, process, remote, unit, observer, timeout):
        self.process = process; self.remote = remote; self.unit = unit
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
        stopped = self.observer(
            self.remote, "systemctl stop " + shlex.quote(self.unit), timeout=15)
        if getattr(stopped, "returncode", 1) != 0:
            raise RemoteImageStageError("helper_failed")
        # Completion is safe only when the exact retained unit is inactive and its cgroup
        # is removed or reports populated=0.  PID/process-group evidence is ignored.
        observed = self.observer(
            self.remote,
            "systemctl show --property=ActiveState --property=ControlGroup --value "
            + shlex.quote(self.unit), timeout=15,
        )
        if getattr(observed, "returncode", 1) != 0:
            raise RemoteImageStageError("helper_failed")
        lines = str(getattr(observed, "stdout", "")).splitlines()
        if not lines or lines[0].strip() != "inactive":
            raise RemoteImageStageError("helper_failed")
        systemd_cgroup = lines[1].strip() if len(lines) > 1 else ""
        process = response.payload.get("process")
        reported_cgroup = process.get("cgroup") if type(process) is dict else None
        if type(reported_cgroup) is not str or self.unit not in reported_cgroup \
                or (systemd_cgroup and systemd_cgroup != reported_cgroup):
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
        collected = self.observer(
            self.remote, "systemctl reset-failed " + shlex.quote(self.unit), timeout=15)
        if getattr(collected, "returncode", 1) != 0:
            raise RemoteImageStageError("helper_failed")
        return response

    def _kill_whole_unit(self) -> None:
        self.observer(self.remote, "systemctl kill --kill-whom=all --signal=SIGKILL "
                      + shlex.quote(self.unit), timeout=15)
        if self.process.poll() is None:
            try: self.process.kill()
            except ProcessLookupError: pass

    def cancel(self) -> dict:
        cgroup_result = self.observer(
            self.remote, "systemctl show --property=ControlGroup --value "
            + shlex.quote(self.unit), timeout=15)
        cgroup = str(getattr(cgroup_result, "stdout", "")).strip()
        self._kill_whole_unit()
        stopped = self.observer(
            self.remote, "systemctl stop " + shlex.quote(self.unit), timeout=15)
        if getattr(stopped, "returncode", 1) != 0:
            return {"unit_inactive": False, "cgroup_empty_or_removed": False,
                    "cleanup_complete": not self.used}
        observed = self.observer(
            self.remote, "systemctl show --property=ActiveState --value "
            + shlex.quote(self.unit), timeout=15)
        inactive = getattr(observed, "returncode", 1) == 0 \
            and str(getattr(observed, "stdout", "")).strip() == "inactive"
        empty = False
        if cgroup:
            checked = self.observer(
                self.remote, "test ! -e " + shlex.quote("/sys/fs/cgroup" + cgroup)
                + " || grep -qx 'populated 0' "
                + shlex.quote("/sys/fs/cgroup" + cgroup + "/cgroup.events"), timeout=15)
            empty = getattr(checked, "returncode", 1) == 0
        return {"unit_inactive": inactive, "cgroup_empty_or_removed": empty,
                "cleanup_complete": not self.used}
