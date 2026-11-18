"""The ``notes`` block of the machine config: which Agent Notes service a remote name means.

    notes:
      default_remote: xspeed-dev
      services:
        xspeed-dev:
          url: https://notes.xspeedcdn.online
          token: {source: personal, key: SANDBOX_NOTES_TOKEN_XSPEED_DEV}   # optional

The block holds no secret. ``token`` names where the secrets broker keeps the
bearer token; it defaults to the ``personal`` source and a key derived from
the remote name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from urllib.parse import urlparse

from .errors import NotesError

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_REFERENCE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def default_token_key(remote: str) -> str:
    return "SANDBOX_NOTES_TOKEN_" + re.sub(r"[^A-Za-z0-9]", "_", remote).upper()


def service_url(value: object) -> str:
    """Return a bare https origin (no path, query, fragment or credentials).

    Plain http is accepted only for a loopback host (a local test service).
    """
    if isinstance(value, str):
        parsed = urlparse(value.strip())
        loopback = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
        if ((parsed.scheme == "https" or loopback) and parsed.hostname and not parsed.username
                and not parsed.password and parsed.path in ("", "/")
                and not parsed.query and not parsed.fragment):
            return f"{parsed.scheme}://{parsed.netloc}"
    raise NotesError("notes_config_invalid", "the notes service url must be an https origin, "
                     "e.g. https://notes.example.com")


@dataclass(frozen=True)
class NotesService:
    remote: str
    url: str
    token_source: str
    token_key: str

    @property
    def endpoint(self) -> str:
        return self.url + "/mcp"


def remote_name(value: object) -> str:
    if isinstance(value, str) and _NAME.fullmatch(value):
        return value
    raise NotesError("notes_config_invalid", "remote names use letters, digits, '.', '_' and '-'")


def resolve(cfg: Mapping | None, remote: str | None) -> NotesService:
    block = (cfg or {}).get("notes") if isinstance(cfg, Mapping) else None
    block = block if isinstance(block, Mapping) else {}
    services = block.get("services") if isinstance(block.get("services"), Mapping) else {}
    chosen = remote or block.get("default_remote")
    if not chosen:
        raise NotesError(
            "notes_remote_missing",
            "no notes service selected: pass --remote NAME or set notes.default_remote "
            "(`sb notes login --remote NAME --url URL` writes both)")
    chosen = remote_name(chosen)
    entry = services.get(chosen)
    if not isinstance(entry, Mapping) or not entry.get("url"):
        raise NotesError(
            "notes_remote_unknown",
            f"no notes service is configured for '{chosen}'; run "
            f"`sb notes login --remote {chosen} --url https://...` or `sb notes config ...`")
    token = entry.get("token")
    if token is None:
        source, key = "personal", default_token_key(chosen)
    elif isinstance(token, Mapping) and set(token) <= {"source", "key"}:
        source = token.get("source", "personal")
        key = token.get("key", default_token_key(chosen))
    else:
        raise NotesError("notes_config_invalid",
                         f"notes.services.{chosen}.token takes only source and key, never a value")
    if not (isinstance(source, str) and _SOURCE.fullmatch(source)
            and isinstance(key, str) and _REFERENCE.fullmatch(key)):
        raise NotesError("notes_config_invalid",
                         f"notes.services.{chosen}.token needs broker source and key names")
    return NotesService(chosen, service_url(entry["url"]), source, key)


def write_service(remote: str, url: str, *, make_default: bool | None = None) -> dict:
    """Record ``remote -> url`` in sandbox.local.yml. Returns what changed."""
    from sandbox.core._config import _local_yaml, _write_local_yaml

    remote = remote_name(remote)
    url = service_url(url)
    local = _local_yaml()
    block = local.get("notes")
    block = dict(block) if isinstance(block, Mapping) else {}
    services = dict(block.get("services") or {})
    entry = dict(services.get(remote) or {})
    changed = entry.get("url") != url
    entry["url"] = url
    services[remote] = entry
    block["services"] = services
    default_set = False
    if make_default or (make_default is None and not block.get("default_remote")):
        default_set = block.get("default_remote") != remote
        block["default_remote"] = remote
    local["notes"] = block
    if changed or default_set:
        _write_local_yaml(local)
    return {"remote": remote, "url": url, "changed": changed or default_set,
            "default_remote": block.get("default_remote")}
