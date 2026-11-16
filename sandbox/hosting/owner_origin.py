"""This host's record of how its controller names and routes it (Fix 5).

`sb remote service migrate` (every installer path goes through
``migrate_remote_mcp_service``) writes it on the remote at
``$HOME/sandbox/runtime/owner-origin.json``, next to the spec 072 source
receipt: the controller's remote name for this host, its front door and its
control port. The controller knows all three at install time; the host does
not, and must not guess the front door (it decides how the edge is written).

``sb host ... --here`` reads it to submit the same server CLI job a controller
would, without SSH, and refuses unless it exists, is the owner's own private
file and is bound to the installed unit (marker and runtime revision). The
owner panel reads it, as a label only, to tell which controller remote name
means this server.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

FILENAME = "owner-origin.json"
RELATIVE = Path("sandbox") / "runtime" / FILENAME
SCHEMA_VERSION = 1
KEYS = frozenset({"schema_version", "remote_name", "front_door", "mcp_port",
                  "ownership_marker", "runtime_revision"})
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_HEX24_RE = re.compile(r"[0-9a-f]{24}\Z")
MAX_BYTES = 4096


class OwnerOriginUnavailable(RuntimeError):
    code = "host_here_origin_unavailable"


def payload(remote_name: str, front_door: str, mcp_port: int, ownership_marker: str,
            runtime_revision: str) -> dict:
    """The record a confirmed install writes; raises ValueError on any bad field."""
    record = {"schema_version": SCHEMA_VERSION, "remote_name": remote_name,
              "front_door": front_door, "mcp_port": mcp_port,
              "ownership_marker": ownership_marker, "runtime_revision": runtime_revision}
    return validate(record)


def validate(record: object) -> dict:
    if not isinstance(record, dict) or set(record) != KEYS:
        raise ValueError("owner origin record has the wrong fields")
    if type(record["schema_version"]) is not int or record["schema_version"] != SCHEMA_VERSION:
        raise ValueError("owner origin record schema is unsupported")
    if not isinstance(record["remote_name"], str) or not _NAME_RE.fullmatch(record["remote_name"]):
        raise ValueError("owner origin remote name is invalid")
    if record["front_door"] not in ("caddy", "nginx"):
        raise ValueError("owner origin front door is invalid")
    if type(record["mcp_port"]) is not int or not 1 <= record["mcp_port"] <= 65535:
        raise ValueError("owner origin control port is invalid")
    for key in ("ownership_marker", "runtime_revision"):
        if not isinstance(record[key], str) or not _HEX24_RE.fullmatch(record[key]):
            raise ValueError(f"owner origin {key} is invalid")
    return dict(record)


def path_for(home: str | os.PathLike) -> Path:
    return Path(home) / RELATIVE


def _read_private(path: Path) -> dict:
    """An owner-only regular file, read through one no-follow descriptor."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        raise OwnerOriginUnavailable(
            "this host has no owner origin record; run `sb remote service migrate` "
            "for it from its controller") from None
    except OSError:
        raise OwnerOriginUnavailable("this host's owner origin record is unreadable") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > MAX_BYTES):
            raise OwnerOriginUnavailable("this host's owner origin record is not the owner's private file")
        data = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    try:
        return validate(json.loads(data.decode("utf-8")))
    except (ValueError, UnicodeDecodeError):
        raise OwnerOriginUnavailable("this host's owner origin record is invalid") from None


def read_for_owner(owner) -> dict:
    """The record, only when it belongs to the installed owner unit ``owner``."""
    record = _read_private(path_for(owner.home))
    if (record["ownership_marker"] != owner.ownership_marker
            or record["runtime_revision"] != owner.runtime_revision):
        raise OwnerOriginUnavailable(
            "this host's owner origin record belongs to another install; run "
            "`sb remote service migrate` for it from its controller")
    return record


def remote_label(home: str | os.PathLike | None = None) -> str | None:
    """The controller's name for this host, or None. A display label, never authority."""
    try:
        if home is None:
            import pwd
            home = pwd.getpwuid(os.geteuid()).pw_dir
        return _read_private(path_for(home))["remote_name"]
    except (OwnerOriginUnavailable, OSError, KeyError):
        return None
