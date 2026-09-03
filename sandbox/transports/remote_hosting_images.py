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
import secrets
import shlex
import subprocess
from typing import Callable

from sandbox.hosting.images.staging_models import MAX_STAGE_FRAME_BYTES, canonical_bytes


_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
FIXED_HELPER_ENTRY = "sandbox-image-stage-helper-v1"
FIXED_HELPER_ENTRY_V2 = "sandbox-image-stage-helper-v2"
READY_TIMEOUT_SECONDS = 15
_INODE_EXEC = r'''import hashlib,json,os,re,stat,sys
root,home,expected,entry,manifest_name=sys.argv[1:]
if (entry,manifest_name) not in {('sandbox-image-stage-helper-v1','manifest.json'),('sandbox-image-stage-helper-v2','manifest-v2.json')}: raise SystemExit(68)
if not home.startswith('/') or root.rsplit('/runtime/helpers/image-stage/',1)[0]!=home: raise SystemExit(68)
basename=root.rsplit('/',1)[-1]
if re.fullmatch(r'sha256-[0-9a-f]{64}-revision-[0-9a-f]{40}',basename) is None: raise SystemExit(68)
owner_uid=os.geteuid(); current=''; protected=home+'/runtime/helpers'
dfd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
for part in root.split('/')[1:]:
 child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dfd); info=os.fstat(child)
 current+='/'+part; mode=stat.S_IMODE(info.st_mode)
 if not stat.S_ISDIR(info.st_mode): raise SystemExit(69)
 if current==home or current==home+'/runtime':
  if info.st_uid!=owner_uid or mode&0o022: raise SystemExit(69)
 elif current==protected or current.startswith(protected+'/'):
  if info.st_uid!=owner_uid or mode!=0o700: raise SystemExit(69)
 elif info.st_uid not in {0,owner_uid} or mode&0o022: raise SystemExit(69)
 os.close(dfd); dfd=child
def opened(name,mode):
 fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=dfd); info=os.fstat(fd)
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or stat.S_IMODE(info.st_mode)!=mode or info.st_nlink!=1: raise SystemExit(70)
 return fd
hfd=opened('staging_helper.py',0o500); mfd=opened(manifest_name,0o600)
with os.fdopen(mfd,'rb') as mh: actual=json.loads(mh.read())
if actual!=json.loads(expected): raise SystemExit(71)
if basename!='sha256-'+actual['artifact_digest'].split(':',1)[1]+'-revision-'+actual['runtime_revision']: raise SystemExit(71)
measured=hashlib.sha256()
while True:
 chunk=os.read(hfd,65536)
 if not chunk: break
 measured.update(chunk)
if 'sha256:'+measured.hexdigest()!=actual['artifact_digest']: raise SystemExit(72)
os.lseek(hfd,0,os.SEEK_SET); os.set_inheritable(hfd,True)
os.execv(sys.executable,[sys.executable,f'/proc/self/fd/{hfd}',entry])'''


class RemoteImageStageError(RuntimeError):
    def __init__(self, code: str, *, process: dict | None = None,
                 cleanup: dict | None = None) -> None:
        self.code = code if code in {"remote_unavailable", "helper_failed", "protocol_invalid"} \
            else "helper_failed"
        self.process = process
        self.cleanup = cleanup
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
            if hasattr(process, "read_ready"):
                ready = process.read_ready(READY_TIMEOUT_SECONDS)
            else:
                readable, _, _ = select.select(
                    [process.stdout], [], [], READY_TIMEOUT_SECONDS)
                ready = process.stdout.read(6) if readable else b""
            if ready != b"READY\n":
                raise RemoteImageStageError("helper_failed")
            launch_cgroup = self._observe_launch_authority(
                remote, unit, description, service_uid)
        except Exception:
            try: process.kill()
            except ProcessLookupError: pass
            process_evidence, cleanup_evidence = self._cleanup_failed_launch(
                remote, unit, description, service_uid)
            raise RemoteImageStageError(
                "helper_failed", process=process_evidence, cleanup=cleanup_evidence) from None
        return _PreparedRemoteStage(process, remote, unit, description, launch_cgroup,
                                    self._observe_unit, timeout_seconds)

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
        observed = self._observe_unit(
            remote, "systemctl --user show --property=LoadState --property=ActiveState "
            "--property=Description --property=ControlGroup " + shlex.quote(unit), timeout=15)
        values = _PreparedRemoteStage._properties(observed)
        if getattr(observed, "returncode", 1) != 0:
            return ({"unit_inactive": False, "cgroup_empty_or_removed": False},
                    {"complete": False})
        if (values.get("LoadState") == "not-found"
                and values.get("ActiveState") == "inactive"
                and values.get("Description", "") in {"", unit}
                and values.get("ControlGroup", "") == ""):
            empty = self._observe_unit(
                remote, "test ! -e " + shlex.quote("/sys/fs/cgroup" + expected_cgroup)
                + " || grep -qx 'populated 0' "
                + shlex.quote("/sys/fs/cgroup" + expected_cgroup + "/cgroup.events"), timeout=15)
            safe = getattr(empty, "returncode", 1) == 0
            return ({"unit_inactive": safe, "cgroup_empty_or_removed": safe,
                     "not_launched": True}, {"complete": safe})
        if (values.get("LoadState") == "loaded"
                and values.get("ActiveState") in {"active", "inactive"}
                and isinstance(values.get("Description"), str)
                and values.get("Description") not in {"", description}
                and values.get("ControlGroup", "") in {"", expected_cgroup}):
            # Never kill a deterministic-name incumbent. Its active or retained
            # cgroup also means cleanup of this launch is not independently
            # proven, so durable orchestration must fence rather than replay.
            return ({"unit_inactive": False, "cgroup_empty_or_removed": False},
                    {"complete": False})
        killed = self._observe_unit(
            remote, "systemctl --user kill --kill-whom=all --signal=SIGKILL "
            + shlex.quote(unit), timeout=15)
        stopped = self._observe_unit(
            remote, "systemctl --user stop " + shlex.quote(unit), timeout=15)
        terminal = self._observe_unit(
            remote, "systemctl --user show --property=LoadState --property=ActiveState "
            "--property=Description --property=ControlGroup " + shlex.quote(unit), timeout=15)
        terminal_values = _PreparedRemoteStage._properties(terminal)
        terminal_safe = getattr(terminal, "returncode", 1) == 0 and (
            (terminal_values.get("LoadState") == "not-found"
             and terminal_values.get("ActiveState") == "inactive"
             and terminal_values.get("Description", "") in {"", unit}
             and terminal_values.get("ControlGroup", "") == "")
            or (terminal_values.get("LoadState") == "loaded"
                and terminal_values.get("ActiveState") == "inactive"
                and terminal_values.get("Description") == description
                and terminal_values.get("ControlGroup", "") in {"", expected_cgroup}))
        empty = self._observe_unit(
            remote, "test ! -e " + shlex.quote("/sys/fs/cgroup" + expected_cgroup)
            + " || grep -qx 'populated 0' "
            + shlex.quote("/sys/fs/cgroup" + expected_cgroup + "/cgroup.events"), timeout=15)
        safe = (getattr(killed, "returncode", 1) == 0
                and getattr(stopped, "returncode", 1) == 0 and terminal_safe
                and getattr(empty, "returncode", 1) == 0)
        return ({"unit_inactive": safe, "cgroup_empty_or_removed": safe},
                {"complete": safe})

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
