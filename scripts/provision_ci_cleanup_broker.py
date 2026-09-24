#!/usr/bin/env python3
"""Install Sandbox's narrowly scoped Linux CI cleanup finalizer.

Run from an exact, committed Sandbox runtime on a provisioned Ubuntu remote.
The script makes only fixed, root-owned helper/config/sudoers changes; it does
not start a daemon, inspect secrets, or accept caller-selected deletion roots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import uuid


HELPER_SOURCE = Path(__file__).resolve().parents[1] / "sandbox" / "application" / "ci_cleanup_broker.py"
HELPER_TARGET = Path("/usr/local/libexec/sandbox-ci-cleanup-helper")
CONFIG_DIR = Path("/etc/sandbox-ci-cleanup")
STATE_DIR = Path("/var/lib/sandbox-ci-cleanup")
SUDOERS_DIR = Path("/etc/sudoers.d")
_SAFE_ACCOUNT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,31}\Z")


class ProvisionError(RuntimeError):
    pass


def _run(argv: tuple[str, ...], *, input_bytes: bytes | None = None,
         check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            argv, input=input_bytes, capture_output=True, timeout=30, check=False,
            close_fds=True,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvisionError("privileged installer command unavailable") from exc
    if check and result.returncode != 0:
        raise ProvisionError("privileged installer command failed")
    return result


def _sudo(*argv: str, input_bytes: bytes | None = None,
          check: bool = True) -> subprocess.CompletedProcess:
    return _run(("/usr/bin/sudo", "-n", *argv), input_bytes=input_bytes, check=check)


def _validate_privileged_paths(paths: tuple[tuple[Path, int | None], ...]) -> None:
    payload = json.dumps([[str(path), mode] for path, mode in paths]).encode()
    script = r'''import json,os,stat,sys
items=json.loads(sys.stdin.buffer.read())
for raw, expected_mode in items:
 fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
 parts=[part for part in raw.split('/') if part]
 missing=False
 try:
  for index,part in enumerate(parts):
   try: child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
   except FileNotFoundError:
    missing=True
    break
   st=os.fstat(child)
   if st.st_uid!=0 or stat.S_IMODE(st.st_mode)&0o022: raise SystemExit(4)
   if index==len(parts)-1 and expected_mode is not None and stat.S_IMODE(st.st_mode)!=expected_mode: raise SystemExit(5)
   os.close(fd); fd=child
 finally:
  os.close(fd)
'''
    result = _sudo("/usr/bin/python3", "-c", script, input_bytes=payload,
                   check=False)
    if result.returncode != 0:
        raise ProvisionError("existing privileged cleanup path is unsafe")


def _roots(sandbox_home: Path, owner_uid: int) -> dict[str, dict[str, object]]:
    deployment = sandbox_home / "deploy-src"
    legacy = sandbox_home / "runtime" / "jobs" / "workspaces"
    artifacts = sandbox_home / "runtime" / "workspaces" / "ci-materializations"
    for path in (deployment, legacy, artifacts):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.resolve(strict=True) != path:
            raise ProvisionError("cleanup root path contains a symlink")
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
                or info.st_uid != owner_uid):
            raise ProvisionError("cleanup root ownership or identity is unsafe")
    return {
        key: {"path": str(path), "device": int(info.st_dev), "inode": int(info.st_ino)}
        for key, path in (("deployment", deployment), ("legacy", legacy),
                          ("artifacts", artifacts))
        for info in (path.lstat(),)
    }


def _read_existing(path: Path, *, expected_mode: int) -> bytes | None:
    probe = _sudo("/usr/bin/python3", "-c", r'''import os,stat,sys
path=sys.argv[1]
try: fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
except FileNotFoundError: raise SystemExit(3)
except OSError: raise SystemExit(4)
st=os.fstat(fd)
if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or stat.S_IMODE(st.st_mode)!=int(sys.argv[2],0) or st.st_size>1024*1024: raise SystemExit(4)
data=b''
while len(data)<=1024*1024:
 block=os.read(fd,65536)
 if not block: break
 data+=block
os.write(1,data)
''', str(path), oct(expected_mode), check=False)
    if probe.returncode == 3:
        return None
    if probe.returncode != 0:
        raise ProvisionError("existing privileged cleanup asset is unsafe")
    return probe.stdout


def _atomic_root_file(path: Path, content: bytes, mode: int,
                      *, existing_mode: int | None = None) -> None:
    parent = path.parent
    _sudo("/usr/bin/install", "-d", "-o", "root", "-g", "root", "-m", "0755", str(parent))
    existing = _read_existing(path, expected_mode=existing_mode or mode)
    if existing == content:
        return
    if existing is not None and existing_mode is None:
        raise ProvisionError("managed sudoers entry differs from expected content")
    temporary = parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    _sudo("/usr/bin/tee", str(temporary), input_bytes=content)
    _sudo("/bin/chmod", f"{mode:04o}", str(temporary))
    if path.name.startswith("sandbox-ci-cleanup-") and path.parent == SUDOERS_DIR:
        validate = _sudo("/usr/sbin/visudo", "-cf", str(temporary), check=False)
        if validate.returncode != 0:
            _sudo("/bin/rm", "-f", str(temporary), check=False)
            raise ProvisionError("generated sudoers rule failed validation")
    _sudo("/bin/mv", "-f", str(temporary), str(path))
    if path.parent == SUDOERS_DIR:
        _sudo("/usr/sbin/visudo", "-c")


def provision(*, sandbox_home: Path) -> dict[str, object]:
    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ProvisionError("run as the remote Sandbox owner on Linux, not as root")
    owner_uid = os.getuid()
    owner = pwd.getpwuid(owner_uid)
    if _SAFE_ACCOUNT.fullmatch(owner.pw_name) is None:
        raise ProvisionError("remote Sandbox account name is not safe for sudoers")
    home = Path(owner.pw_dir).resolve(strict=True)
    sandbox_home = sandbox_home.expanduser().resolve(strict=True)
    if sandbox_home != home / "sandbox":
        raise ProvisionError("cleanup broker supports only the default owner Sandbox home")
    if (not HELPER_SOURCE.is_file() or HELPER_SOURCE.is_symlink()
            or HELPER_SOURCE.stat().st_uid != owner_uid):
        raise ProvisionError("cleanup broker source identity is unsafe")
    source = HELPER_SOURCE.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    root_config = {
        "schema_version": 1,
        "owner_uid": owner_uid,
        "roots": _roots(sandbox_home, owner_uid),
    }
    config_bytes = (json.dumps(root_config, sort_keys=True, separators=(",", ":")) + "\n").encode()
    state_user = STATE_DIR / str(owner_uid)

    _validate_privileged_paths((
        (Path("/usr/local/libexec"), 0o755),
        (CONFIG_DIR, 0o755),
        (Path("/var/lib"), None),
        (STATE_DIR, 0o700),
        (state_user, 0o700),
        (state_user / "quarantine", 0o700),
        (state_user / "operations", 0o700),
        (SUDOERS_DIR, None),
    ))
    try:
        quarantine_device = int(_sudo(
            "/usr/bin/stat", "-c", "%d", "/var/lib").stdout.strip())
    except (ValueError, UnicodeError) as exc:
        raise ProvisionError("cleanup quarantine filesystem is unavailable") from exc
    if any(root["device"] != quarantine_device
           for root in root_config["roots"].values()):
        raise ProvisionError("cleanup roots and private quarantine are on different filesystems")

    _sudo("/usr/bin/install", "-d", "-o", "root", "-g", "root", "-m", "0700",
          str(STATE_DIR))
    _sudo("/usr/bin/install", "-d", "-o", "root", "-g", "root", "-m", "0700",
          str(state_user))
    _sudo("/usr/bin/install", "-d", "-o", "root", "-g", "root", "-m", "0700",
          str(state_user / "quarantine"))
    _sudo("/usr/bin/install", "-d", "-o", "root", "-g", "root", "-m", "0700",
          str(state_user / "operations"))
    _sudo("/usr/bin/install", "-d", "-o", "root", "-g", "root", "-m", "0755",
          "/usr/local/libexec", str(CONFIG_DIR))
    _read_existing(HELPER_TARGET, expected_mode=0o755)
    _sudo("/usr/bin/install", "-o", "root", "-g", "root", "-m", "0755",
          str(HELPER_SOURCE), str(HELPER_TARGET))
    installed_digest = _sudo("/usr/bin/sha256sum", str(HELPER_TARGET)).stdout.decode("ascii", "replace").split()[0]
    if installed_digest != digest:
        raise ProvisionError("installed cleanup helper digest mismatch")
    _atomic_root_file(CONFIG_DIR / f"{owner_uid}.json", config_bytes, 0o600,
                      existing_mode=0o600)

    sudoers_name = f"sandbox-ci-cleanup-{owner_uid}"
    sudoers_path = SUDOERS_DIR / sudoers_name
    sudoers_line = f"{owner.pw_name} ALL=(root) NOPASSWD: {HELPER_TARGET}\n".encode()
    current = _read_existing(sudoers_path, expected_mode=0o440)
    if current is not None and current != sudoers_line:
        raise ProvisionError("existing cleanup sudoers entry is not the expected managed rule")
    if current is None:
        _atomic_root_file(sudoers_path, sudoers_line, 0o440)
    else:
        _sudo("/usr/sbin/visudo", "-cf", str(sudoers_path))

    capability = _sudo(str(HELPER_TARGET), "capability", check=False)
    try:
        result = json.loads(capability.stdout)
    except (TypeError, ValueError):
        result = {}
    if capability.returncode != 0 or result.get("ok") is not True:
        raise ProvisionError("installed cleanup broker failed its capability check")
    return {"ok": True, "status": "installed", "owner_uid": owner_uid,
            "helper_digest": f"sha256:{digest}", "roots_pinned": 3}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-home", default=str(Path.home() / "sandbox"))
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        response = {"ok": True, "status": "planned", "requires_confirm": True,
                    "action": "install-ci-cleanup-broker"}
        print(json.dumps(response, sort_keys=True))
        return 0
    try:
        response = provision(sandbox_home=Path(args.sandbox_home))
    except (OSError, ProvisionError) as exc:
        response = {"ok": False, "status": "failed", "code": "cleanup_broker_install_failed",
                    "message": str(exc)[:200]}
    print(json.dumps(response, sort_keys=True))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
