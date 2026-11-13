"""Declare, render, and issue links for the Sandbox login service.

A project opts in through ``autologin.login_service`` plus named
``autologin.entries`` in its hosting manifest. See docs/login-service.md.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import shlex
import urllib.parse
import zlib
from dataclasses import dataclass
from pathlib import Path

SERVICE_NAME = "sandbox-login"
STATE_KEY = "login_default_email"
LINK_PARAMETER = "sandbox_autologin"
RECORD_SCHEMA = "sandbox.login-token.v1"
STATE_DIRECTORY = "/run/sandbox-login"
INTERNAL_PORT = 8080
HEALTH_PORT = 8081
MAX_ENABLED_DAYS = 30
MIN_SESSION_SECONDS = 300
MAX_SESSION_SECONDS = 172800
MIN_LINK_SECONDS = 60
MAX_LINK_SECONDS = 3600
DEFAULT_LINK_SECONDS = 900
MAX_POLL_SECONDS = 30
CAPTURE_ADAPTERS = ("mailpit_v1",)
ENTRY_MODES = ("magic_link", "inbox_gate", "site_gate")
# Modes that read the capture service; a declaration of only site_gate entries needs none.
CAPTURE_MODES = ("magic_link", "inbox_gate")
SITE_PATH_PREFIX = "/__sandbox/share/"
SITE_MAX_USES = 20
SITE_DEFAULT_USES = 1
# Links and sessions are files on the tmpfs, each taking at least one page.
SITE_STATE_TMPFS_SIZE = "8m"
# Spec 081: the ``share:`` block and the gate's own published port.
SHARE_ENTRY = "share"
DEFAULT_SHARE_SESSION_SECONDS = 43200
GATE_PORT_OFFSET = 3000
# The runtime travels as one argv string, and Linux caps a single argument at
# 128 KiB (MAX_ARG_STRLEN). Compressed, it stays far below that.
_LOADER = "import base64,sys,zlib;exec(compile(zlib.decompress(base64.b64decode(sys.argv[1])),'login_service','exec'))"

_ENTRY_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
_PINNED_IMAGE = re.compile(r"[a-z0-9][a-z0-9./_:-]*@sha256:[0-9a-f]{64}")
_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)
_SERVICE_FIELDS = {"image", "enabled_until", "session_ttl_seconds", "allowed_host_suffixes", "capture"}
_CAPTURE_FIELDS = {"service", "adapter", "api_url", "username", "credential_secret"}
_ENTRY_FIELDS = {"mode", "requires_email", "hostname", "path", "ttl_seconds", "magic_link", "landing_path",
                 "upstream"}
_SHARE_FIELDS = {"hostname", "private", "agent_mint", "stack_sign_in", "route_sign_in", "inbox_sign_in", "linked_access"}
_SHARE_SERVICE_FIELDS = {"image", "enabled_until", "session_ttl_seconds", "allowed_host_suffixes"}
_MAGIC_FIELDS = {"request_url", "request_origin", "link_origin", "link_path", "link_pattern",
                 "required_params", "poll_timeout_seconds", "denied_emails"}


class LoginServiceError(Exception):
    """A finite, named refusal."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _invalid(message: str) -> LoginServiceError:
    return LoginServiceError("login_service_invalid", message)


def is_declared(raw: object) -> bool:
    return isinstance(raw, dict) and ("entries" in raw or "login_service" in raw)


def _reject_unknown(raw: dict, allowed: set[str], where: str) -> None:
    extra = sorted(set(raw) - allowed)
    if extra:
        raise _invalid(f"{where} has unknown fields: {', '.join(extra)}")


def _bounded_int(value: object, low: int, high: int, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _invalid(f"{where} must be an integer from {low} to {high}")
    return value


def _url_path(value: object, where: str) -> str:
    path = str(value or "").strip()
    if (not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path
            or ".." in path.split("/") or _has_unsafe_characters(path)):
        raise _invalid(f"{where} must be an absolute URL path without a query or fragment")
    return path


def _has_unsafe_characters(value: str) -> bool:
    return "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value)


def _host_is_covered(hostname: str, suffixes: list[str]) -> bool:
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes)


def _https_url(value: str) -> urllib.parse.SplitResult | None:
    """Split an https URL with a host and no credentials, or return None."""
    if _has_unsafe_characters(value):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port  # noqa: B018 -- reading it validates the port
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or "@" in parsed.netloc:
        return None
    return parsed


def _origin(value: object, where: str, suffixes: list[str]) -> str:
    text = str(value or "").strip()
    parsed = _https_url(text)
    port = f":{parsed.port}" if parsed is not None and parsed.port is not None else ""
    if parsed is None or text != f"https://{parsed.hostname}{port}":
        raise _invalid(f"{where} must be a lowercase https origin with no credentials or path")
    if not _host_is_covered(parsed.hostname, suffixes):
        raise _invalid(f"{where} is not covered by allowed_host_suffixes")
    return text


def _return_destination(value: str, where: str, suffixes: list[str]) -> str:
    """A redirect destination is a local path or an https URL on an allowed host."""
    if value.startswith("/"):
        return _url_path(value, where)
    parsed = _https_url(value)
    if parsed is None or not _host_is_covered(parsed.hostname, suffixes):
        raise _invalid(f"{where} must be a local path or an https URL on an allowed host")
    return value


def _internal_url(value: object, hosts: set[str], where: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if (parsed.scheme != "http" or parsed.hostname not in hosts or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise _invalid(f"{where} must be an http URL on a service of this project")
    try:
        if parsed.port is None:
            raise ValueError
    except ValueError:
        raise _invalid(f"{where} must name a port") from None
    return parsed


def parse_enabled_until(value: object) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value or ""))
    except ValueError:
        raise _invalid("login_service.enabled_until must be a YYYY-MM-DD date") from None


def _parse_capture(raw: object, *, primary: str, background: list[str], secrets: dict) -> dict:
    if not isinstance(raw, dict):
        raise _invalid("login_service.capture must be a mapping")
    _reject_unknown(raw, _CAPTURE_FIELDS, "login_service.capture")
    service = str(raw.get("service") or "").strip()
    if service == primary:
        raise _invalid("login_service.capture.service must not be the service Sandbox routes to")
    if service not in background:
        raise _invalid("login_service.capture.service must be a background service of this project")
    if raw.get("adapter") not in CAPTURE_ADAPTERS:
        raise _invalid("login_service.capture.adapter must be one of: " + ", ".join(CAPTURE_ADAPTERS))
    url = _internal_url(raw.get("api_url"), {service}, "login_service.capture.api_url")
    if ("username" in raw) != ("credential_secret" in raw):
        raise _invalid("login_service.capture needs both username and credential_secret, or neither")
    if "username" not in raw:
        return {"service": service, "adapter": raw["adapter"], "api_url": url.geturl(),
                "username": None, "credential_secret": None}
    username = str(raw.get("username") or "").strip()
    if not username or ":" in username:
        raise _invalid("login_service.capture.username must be a non-empty name without a colon")
    credential = str(raw.get("credential_secret") or "").strip()
    if credential not in {*secrets.get("required", {}), *secrets.get("generated", {})}:
        raise _invalid("login_service.capture.credential_secret must name a required or generated secret")
    return {"service": service, "adapter": raw["adapter"], "api_url": url.geturl(),
            "username": username, "credential_secret": credential}


def _parse_magic_link(raw: object, *, services: set[str], where: str, suffixes: list[str]) -> dict:
    if not isinstance(raw, dict):
        raise _invalid(f"{where}.magic_link must be a mapping")
    _reject_unknown(raw, _MAGIC_FIELDS, f"{where}.magic_link")
    prefix = f"{where}.magic_link"
    request = _internal_url(raw.get("request_url"), services, f"{prefix}.request_url")
    pattern = str(raw.get("link_pattern") or "")
    if not pattern or len(pattern) > 500:
        raise _invalid(f"{prefix}.link_pattern is required and at most 500 characters")
    try:
        compiled = re.compile(pattern)
    except re.error:
        raise _invalid(f"{prefix}.link_pattern is not a valid expression") from None
    if compiled.groups:
        raise _invalid(f"{prefix}.link_pattern must not use capturing groups")
    required = raw.get("required_params", {})
    if not isinstance(required, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in required.items()):
        raise _invalid(f"{prefix}.required_params must map names to exact values")
    if "return" in required:
        _return_destination(required["return"], f"{prefix}.required_params.return", suffixes)
    raw_denied = raw.get("denied_emails", [])
    if not isinstance(raw_denied, list):
        raise _invalid(f"{prefix}.denied_emails must be a list of email addresses")
    try:
        denied = [validate_email(item).casefold() for item in raw_denied]
    except LoginServiceError:
        raise _invalid(f"{prefix}.denied_emails must be a list of email addresses") from None
    return {
        "request_url": request.geturl(),
        "request_origin": _origin(raw.get("request_origin"), f"{prefix}.request_origin", suffixes),
        "link_origin": _origin(raw.get("link_origin"), f"{prefix}.link_origin", suffixes),
        "link_path": _url_path(raw.get("link_path"), f"{prefix}.link_path"),
        "link_pattern": pattern,
        "required_params": dict(required),
        "denied_emails": denied,
        "poll_timeout_seconds": _bounded_int(
            raw.get("poll_timeout_seconds", 10), 1, MAX_POLL_SECONDS, f"{prefix}.poll_timeout_seconds"),
    }


def _gate_hostname(raw: dict, gate_hosts: set[str], where: str) -> str:
    hostname = str(raw.get("hostname") or "").strip().rstrip(".").lower()
    if hostname not in gate_hosts:
        raise _invalid(f"{where}.hostname must be a served alias of this target, not its primary hostname")
    return hostname


def _parse_site_gate(name: str, raw: dict, *, where: str, gate_hosts: set[str], upstreams: set[str]) -> dict:
    """A share-link gate in front of one background service of this project."""
    if raw.get("requires_email", False) is not False:
        raise _invalid(f"{where}.requires_email must be false for site_gate")
    if "magic_link" in raw:
        raise _invalid(f"{where}.magic_link applies only to magic_link entries")
    hostname = _gate_hostname(raw, gate_hosts, where)
    path = _url_path(raw.get("path", f"{SITE_PATH_PREFIX}{name}"), f"{where}.path")
    if path == "/":
        raise _invalid(f"{where}.path must be a dedicated path, not /")
    upstream = _internal_url(raw.get("upstream"), upstreams, f"{where}.upstream")
    if upstream.path not in ("", "/") or not 0 < upstream.port < 65536:
        raise _invalid(f"{where}.upstream must be http://<background service>:<port> with no path")
    landing = _url_path(raw.get("landing_path", "/"), f"{where}.landing_path")
    if landing == path:
        raise _invalid(f"{where}.landing_path must differ from {where}.path")
    return {
        "mode": "site_gate", "requires_email": False, "hostname": hostname, "path": path,
        "ttl_seconds": _bounded_int(raw.get("ttl_seconds", DEFAULT_LINK_SECONDS), MIN_LINK_SECONDS,
                                    MAX_LINK_SECONDS, f"{where}.ttl_seconds"),
        "upstream": f"http://{upstream.hostname}:{upstream.port}",
        "landing_path": landing,
    }


def _parse_entry(name: object, raw: object, *, gate_hosts: set[str], services: set[str],
                 suffixes: list[str], upstreams: set[str]) -> dict:
    if not isinstance(name, str) or not _ENTRY_NAME.fullmatch(name):
        raise _invalid("entry name must be lowercase letters, digits, or hyphens")
    where = f"entries.{name}"
    if not isinstance(raw, dict):
        raise _invalid(f"{where} must be a mapping")
    _reject_unknown(raw, _ENTRY_FIELDS, where)
    mode = raw.get("mode")
    if mode not in ENTRY_MODES:
        raise _invalid(f"{where}.mode must be one of: " + ", ".join(ENTRY_MODES))
    if mode == "site_gate":
        return _parse_site_gate(name, raw, where=where, gate_hosts=gate_hosts, upstreams=upstreams)
    if "upstream" in raw:
        raise _invalid(f"{where}.upstream applies only to site_gate entries")
    needs_email = raw.get("requires_email")
    if not isinstance(needs_email, bool) or needs_email is not (mode == "magic_link"):
        raise _invalid(f"{where}.requires_email must be true for magic_link and false for inbox_gate")
    hostname = _gate_hostname(raw, gate_hosts, where)
    path = _url_path(raw.get("path"), f"{where}.path")
    if path == "/":
        raise _invalid(f"{where}.path must be a dedicated path, not /")
    entry = {
        "mode": mode, "requires_email": needs_email, "hostname": hostname, "path": path,
        "ttl_seconds": _bounded_int(raw.get("ttl_seconds", DEFAULT_LINK_SECONDS), MIN_LINK_SECONDS,
                                    MAX_LINK_SECONDS, f"{where}.ttl_seconds"),
    }
    if mode == "magic_link":
        entry["magic_link"] = _parse_magic_link(
            raw.get("magic_link"), services=services, where=where, suffixes=suffixes)
    else:
        if "magic_link" in raw:
            raise _invalid(f"{where}.magic_link applies only to magic_link entries")
        entry["landing_path"] = _url_path(raw.get("landing_path", "/"), f"{where}.landing_path")
    return entry


def _check_suffixes(raw: object, routes: list[dict]) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise _invalid("login_service.allowed_host_suffixes is required")
    suffixes = []
    for item in raw:
        suffix = str(item or "").strip().lstrip(".").lower()
        if "." not in suffix or "*" in suffix or "/" in suffix:
            raise _invalid("login_service.allowed_host_suffixes must be domain names")
        suffixes.append(suffix)
    for route in routes:
        hostname = route["hostname"].removeprefix("*.")
        if not _host_is_covered(hostname, suffixes):
            raise _invalid(f"allowed_host_suffixes does not cover target hostname {hostname}")
    return suffixes


def validate_linked_access(value: object, suffixes: list[str] | None = None) -> dict[str, str]:
    """Validate author intent for exact recorded links, never measured app access."""
    if not isinstance(value, dict) or len(value) > 16:
        raise _invalid("share.linked_access must map at most 16 exact hostnames to literal open")
    host_pattern = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
    for host, access in value.items():
        if (not isinstance(host, str) or len(host) > 253
                or not re.fullmatch(host_pattern, host) or access != "open"):
            raise _invalid("share.linked_access requires exact canonical DNS keys and literal open values")
        if suffixes is not None and not _host_is_covered(host, suffixes):
            raise _invalid("share.linked_access hostname is not covered by allowed_host_suffixes")
    return dict(value)


def _share_block(share: object, *, declared_service: bool) -> tuple[dict, dict | None]:
    """Split ``share:`` into its own fields and, in the short form, login-service fields."""
    if not isinstance(share, dict):
        raise _invalid("share must be a mapping")
    allowed = _SHARE_FIELDS | (set() if declared_service else _SHARE_SERVICE_FIELDS)
    extra = sorted(set(share) - allowed)
    if extra:
        hint = (" (autologin.login_service already declares them)" if declared_service
                and set(extra) & _SHARE_SERVICE_FIELDS else "")
        raise _invalid(f"share has unknown fields: {', '.join(extra)}{hint}")
    for flag in ("private", "agent_mint"):
        if not isinstance(share.get(flag, False), bool):
            raise _invalid(f"share.{flag} must be true or false")
    sign_in = share.get("stack_sign_in", [])
    if not isinstance(sign_in, list) or not all(isinstance(item, str) for item in sign_in) \
            or len(set(sign_in)) != len(sign_in):
        raise _invalid("share.stack_sign_in must be a list of distinct autologin entry names")
    route_sign_in = share.get("route_sign_in", {})
    if (not isinstance(route_sign_in, dict) or len(route_sign_in) > 16
            or not all(isinstance(host, str) and isinstance(name, str)
                       for host, name in route_sign_in.items())):
        raise _invalid("share.route_sign_in must map at most 16 hostnames to entry names or $wordpress")
    linked_access = validate_linked_access(share.get("linked_access", {}))
    inbox = share.get("inbox_sign_in")
    if inbox is not None and (not isinstance(inbox, str) or not _ENTRY_NAME.fullmatch(inbox)):
        raise _invalid("share.inbox_sign_in must be a literal inbox_gate entry name")
    own = {"hostname": str(share.get("hostname") or "").strip().rstrip(".").lower(),
           "private": share.get("private", False), "agent_mint": share.get("agent_mint", False),
           "stack_sign_in": list(sign_in), "route_sign_in": dict(route_sign_in),
           "inbox_sign_in": inbox, "linked_access": linked_access}
    if declared_service:
        return own, None
    service = {key: share[key] for key in _SHARE_SERVICE_FIELDS if key in share}
    service.setdefault("session_ttl_seconds", DEFAULT_SHARE_SESSION_SECONDS)
    return own, service


def parse_declaration(raw: object, *, routes: list[dict], compose: dict, secrets: dict,
                      share: object = None) -> dict | None:
    """Return the normalized declaration, or None when none is declared.

    ``share`` is the environment's ``share:`` block (spec 081). Without
    ``autologin.login_service`` it carries the service fields itself; either way it
    adds the reserved ``share`` site_gate entry in front of the routed service.
    """
    declared = is_declared(raw)
    if not declared and share is None:
        return None
    share_own = None
    if declared:
        if set(raw) != {"login_service", "entries"}:
            raise _invalid("autologin must hold exactly login_service and entries in this form")
        service, entries = raw["login_service"], raw["entries"]
        if share is not None:
            share_own, _ = _share_block(share, declared_service=True)
    else:
        share_own, service = _share_block(share, declared_service=False)
        entries = {}
    if share_own is not None and isinstance(entries, dict) and SHARE_ENTRY in entries:
        raise _invalid(f"entries.{SHARE_ENTRY} is reserved for the share block")
    if not isinstance(service, dict):
        raise _invalid("login_service must be a mapping")
    _reject_unknown(service, _SERVICE_FIELDS, "login_service")
    primary = str(compose["service"])
    background = list(compose.get("background_services") or [])
    if SERVICE_NAME in {primary, *background, *(compose.get("init_services") or [])}:
        raise _invalid(f"{SERVICE_NAME} is a reserved Compose service name")
    if "enabled_until" not in service:
        raise _invalid("login_service.enabled_until is required")
    enabled_until = parse_enabled_until(service["enabled_until"])
    if "session_ttl_seconds" not in service:
        raise _invalid("login_service.session_ttl_seconds is required")
    session = _bounded_int(service["session_ttl_seconds"], MIN_SESSION_SECONDS, MAX_SESSION_SECONDS,
                           "login_service.session_ttl_seconds")
    image = str(service.get("image") or "").strip()
    if not _PINNED_IMAGE.fullmatch(image):
        raise _invalid("login_service.image must be pinned as name@sha256:digest")
    suffixes = _check_suffixes(service.get("allowed_host_suffixes"), routes)
    if share_own is not None:
        validate_linked_access(share_own["linked_access"], suffixes)
    if not isinstance(entries, dict) or (not entries and share_own is None):
        raise _invalid("entries must be a non-empty mapping")
    needs_capture = any(not isinstance(item, dict) or item.get("mode") != "site_gate"
                        for item in entries.values())
    capture = None
    if needs_capture or "capture" in service:
        # Without a capture-reading entry the block is optional, but a given one is still checked.
        capture = _parse_capture(service.get("capture"), primary=primary, background=background,
                                 secrets=secrets)
    gate_hosts = {route["hostname"] for route in routes
                  if route["mode"] == "serve" and not route.get("primary")
                  and not route["hostname"].startswith("*.")}
    callable_services = {primary, *background} - ({capture["service"]} if capture else set())
    parsed = {name: _parse_entry(name, item, gate_hosts=gate_hosts, services=callable_services,
                                suffixes=suffixes, upstreams=set(background))
              for name, item in entries.items()}
    if share_own is not None:
        parsed[SHARE_ENTRY] = _share_entry(share_own, gate_hosts=gate_hosts, compose=compose)
        for name in share_own["stack_sign_in"]:
            if parsed.get(name, {}).get("mode") not in ("magic_link", "inbox_gate"):
                raise _invalid(f"share.stack_sign_in names {name!r}, which is not a magic_link or "
                               "inbox_gate entry")
    if share_own is not None:
        validate_route_sign_in(share_own, parsed, routes,
                               wordpress=raw if not declared and isinstance(raw, dict) else None)
    site_hosts = [entry["hostname"] for entry in parsed.values() if entry["mode"] == "site_gate"]
    if any(sum(1 for entry in parsed.values() if entry["hostname"] == host) > 1 for host in site_hosts):
        # The gate answers every path on its hostname, so nothing else may share it.
        raise _invalid("a site_gate hostname must front no other entry")
    seen, inbox_hosts = set(), set()
    for entry in parsed.values():
        location = (entry["hostname"], entry["path"])
        if location in seen:
            raise _invalid("entry path must be unique per hostname")
        seen.add(location)
        if entry["mode"] == "inbox_gate":
            if entry["hostname"] in inbox_hosts:
                raise _invalid("a hostname can front only one inbox_gate entry")
            inbox_hosts.add(entry["hostname"])
            # While a link is live the gate passes visitors to the capture
            # service's own login; with no login there it would be open access.
            if capture["username"] is None:
                raise _invalid("inbox_gate entries need a capture login (username and credential_secret)")
    return {"image": image, "enabled_until": enabled_until.isoformat(),
            "session_ttl_seconds": session, "allowed_host_suffixes": suffixes,
            "capture": capture, "entries": parsed, "share": share_own}


def validate_route_sign_in(share: dict, entries: dict, routes: list[dict], *,
                          wordpress: dict | None) -> None:
    """Check exact destinations. $wordpress is reserved, never an entry name."""
    served = {route["hostname"] for route in routes if route.get("mode") == "serve"}
    primary = next((route["hostname"] for route in routes if route.get("primary")), None)
    for host, name in share.get("route_sign_in", {}).items():
        if host not in served:
            raise _invalid("share.route_sign_in keys must be served route hostnames")
        if name == "$wordpress":
            if not wordpress or host != primary:
                raise _invalid("share.route_sign_in $wordpress requires the WordPress primary route")
            continue
        if name not in share["stack_sign_in"]:
            raise _invalid("share.route_sign_in values must be listed in share.stack_sign_in")
        entry = entries.get(name, {})
        destination = (urllib.parse.urlsplit(entry["magic_link"]["link_origin"]).hostname
                       if entry.get("mode") == "magic_link" else entry.get("hostname"))
        if entry.get("mode") not in ("magic_link", "inbox_gate") or destination != host:
            raise _invalid("share.route_sign_in destination hostname does not match its entry")
    inbox = share.get("inbox_sign_in")
    if inbox is not None and (inbox not in share["stack_sign_in"]
                              or entries.get(inbox, {}).get("mode") != "inbox_gate"):
        raise _invalid("share.inbox_sign_in must name a listed inbox_gate entry")


def _share_entry(share: dict, *, gate_hosts: set[str], compose: dict) -> dict:
    """The reserved site_gate entry a ``share:`` block puts in front of the routed service."""
    if share["hostname"] not in gate_hosts:
        raise _invalid("share.hostname must be a served alias of this target, not its primary hostname")
    port = int(compose["container_port"])
    return {"mode": "site_gate", "requires_email": False, "hostname": share["hostname"],
            "path": f"{SITE_PATH_PREFIX}{SHARE_ENTRY}", "ttl_seconds": DEFAULT_LINK_SECONDS,
            "upstream": f"http://{compose['service']}:{port}", "landing_path": "/"}


def enabled_until_epoch(declaration: dict) -> int:
    """The declared date counts through its end, UTC."""
    date = parse_enabled_until(declaration["enabled_until"])
    end = dt.datetime(date.year, date.month, date.day, tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
    return int(end.timestamp())


def assert_applicable(declaration: dict, *, now: float) -> None:
    if now >= enabled_until_epoch(declaration):
        raise LoginServiceError("login_service_expired", "autologin.login_service.enabled_until has passed")
    today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    if (parse_enabled_until(declaration["enabled_until"]) - today).days > MAX_ENABLED_DAYS:
        raise LoginServiceError(
            "login_service_expiry_too_far",
            f"autologin.login_service.enabled_until must be within {MAX_ENABLED_DAYS} days")


def has_site_gate(declaration: dict | None) -> bool:
    return bool(declaration) and any(
        entry["mode"] == "site_gate" for entry in declaration["entries"].values())


def share_declaration(declaration: dict | None) -> dict | None:
    """The ``share:`` part of a declaration (hostname, private, agent_mint, stack_sign_in)."""
    return (declaration or {}).get("share")


def is_private(declaration: dict | None) -> bool:
    return bool((share_declaration(declaration) or {}).get("private"))


def gate_hostnames(declaration: dict | None) -> set[str]:
    """Every hostname the front door must send to the gate rather than the application."""
    if not declaration:
        return set()
    return {entry["hostname"] for entry in declaration["entries"].values()}


def gate_loopback_port(port: int) -> int:
    """The gate's managed loopback port: a fixed offset from the target's own port."""
    return int(port) + GATE_PORT_OFFSET


def site_upstream_services(declaration: dict) -> list[str]:
    """Compose services that a site_gate entry forwards to."""
    return sorted({urllib.parse.urlsplit(entry["upstream"]).hostname
                   for entry in declaration["entries"].values() if entry["mode"] == "site_gate"})


def status_view(declaration: dict, *, now: float) -> dict:
    return {
        "state": "expired" if now >= enabled_until_epoch(declaration) else "enabled",
        "enabled_until": declaration["enabled_until"],
        "entries": [{"name": name, "mode": entry["mode"], "hostname": entry["hostname"],
                     "requires_email": entry["requires_email"]}
                    for name, entry in sorted(declaration["entries"].items())],
    }


def runtime_config(declaration: dict, *, target_digest: str | None = None) -> dict:
    """What the running service needs; never carries a secret or its name."""
    from sandbox.hosting.owner_share_record import digest
    capture = declaration["capture"]
    entries = {}
    for name, entry in declaration["entries"].items():
        entries[name] = dict(entry)
        if "magic_link" in entry:
            # Refused at issue, so these addresses never need to reach the server.
            entries[name]["magic_link"] = {
                key: value for key, value in entry["magic_link"].items() if key != "denied_emails"}
    return {
        "enabled_until_epoch": enabled_until_epoch(declaration),
        "session_ttl_seconds": declaration["session_ttl_seconds"],
        "capture": ({key: capture[key] for key in ("adapter", "api_url", "username")}
                    if capture else None),
        "entries": entries,
        # A stack page probes linked sites over public HTTPS only on these hosts.
        "allowed_host_suffixes": list(declaration["allowed_host_suffixes"]),
        # Public applied policy, bound by the owner-panel gate capability digest.
        "panel_share": dict(declaration.get("share") or {}),
        "panel_target_digest": target_digest,
        "panel_access_digest": digest({key: declaration[key] for key in
            ("enabled_until", "allowed_host_suffixes", "session_ttl_seconds", "entries", "share") if key in declaration}),
    }


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def render_service(declaration: dict, gate_port: int | None = None, *, target_digest: str | None = None) -> str:
    """Render the Compose service block; it joins the override's ``services:``.

    With ``gate_port`` the service publishes on that loopback port only, so the front
    door can send gate hostnames straight to it (spec 081).
    """
    source = _runtime_source()
    config = _b64(json.dumps(runtime_config(declaration, target_digest=target_digest), sort_keys=True, separators=(",", ":")).encode())
    probe = f"import urllib.request as u;u.urlopen('http://127.0.0.1:{HEALTH_PORT}/healthz',timeout=3)"
    capture = declaration["capture"]
    started_after = sorted({*([capture["service"]] if capture else []), *site_upstream_services(declaration)})
    size = SITE_STATE_TMPFS_SIZE if has_site_gate(declaration) else "1m"
    lines = [
        f"  {SERVICE_NAME}:",
        f"    image: {json.dumps(declaration['image'])}",
        '    restart: "unless-stopped"',
        '    user: "65534:65534"',
        "    read_only: true",
        "    cap_drop: [\"ALL\"]",
        "    security_opt: [\"no-new-privileges:true\"]",
        f"    tmpfs: [{json.dumps(STATE_DIRECTORY + f':mode=0700,uid=65534,gid=65534,size={size}')}]",
        f"    expose: [\"{INTERNAL_PORT}\"]",
        *([f"    ports: [\"127.0.0.1:{int(gate_port)}:{INTERNAL_PORT}\"]"] if gate_port else []),
        *(["    depends_on:"] if started_after else []),
        *(line for name in started_after
          for line in (f"      {name}:", "        condition: service_started")),
        "    environment:",
        f'      SANDBOX_LOGIN_CONFIG: "{config}"',
        *([f'      SANDBOX_LOGIN_CAPTURE_CREDENTIAL: "${{{capture["credential_secret"]}}}"']
          if capture and capture["credential_secret"] else []),
        f"    command: {json.dumps(['python3', '-c', _LOADER, source])}",
        "    healthcheck:",
        f"      test: {json.dumps(['CMD', 'python3', '-c', probe])}",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 12",
    ]
    return "\n".join(lines) + "\n"


def _runtime_source() -> str:
    return _b64(zlib.compress(Path(__file__).with_name("runtime.py").read_bytes(), 9))


def preflight_upstreams(declaration: dict, primary: str) -> list[str]:
    """Services whose ports must stay on loopback, or the gate could be bypassed.

    A background site_gate upstream always; the routed service only when the target is
    private, because otherwise it is public through the front door anyway.
    """
    services = {urllib.parse.urlsplit(entry["upstream"]).hostname
                for name, entry in declaration["entries"].items()
                if entry["mode"] == "site_gate" and name != SHARE_ENTRY}
    if is_private(declaration):
        services.add(primary)
    return sorted(services)


def preflight_command(prefix: str, capture_service: str | None, upstreams: list[str] = (),
                      gate_port: int | None = None) -> str:
    """A read-only remote probe; its output is counts, never Compose values.

    ``login_ports`` counts every published port of the login service except the one
    managed loopback mapping ``127.0.0.1:<gate_port>:8080``.
    """
    program = "\n".join((
        "import json,subprocess,sys",
        "command,capture,login,upstreams,gate=sys.argv[1:6]",
        "upstreams=json.loads(upstreams)",
        "done=subprocess.run(command,shell=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=60,check=True)",
        "services=json.loads(done.stdout)['services']",
        "LOOPBACK={'127.0.0.1','::1'}",
        "def ports(name):return (services.get(name) or {}).get('ports') or []",
        "def host_network(name):return 1 if (services.get(name) or {}).get('network_mode')=='host' else 0",
        "def beyond_loopback(port):return not (isinstance(port,dict) and port.get('host_ip') in LOOPBACK)",
        "capture_ports=sum(1 for port in ports(capture) if beyond_loopback(port))+host_network(capture)",
        "def managed(port):return isinstance(port,dict) and port.get('host_ip')=='127.0.0.1' and str(port.get('published'))==gate and str(port.get('target'))=='8080'",
        "login_ports=sum(1 for port in ports(login) if not managed(port))+host_network(login)",
        "upstream_missing=sum(1 for name in upstreams if name not in services)",
        "upstream_ports=sum(sum(1 for port in ports(name) if beyond_loopback(port))+host_network(name) for name in upstreams)",
        "print(json.dumps({'capture_present':(capture in services) if capture else True,'capture_ports':capture_ports,'login_ports':login_ports,'upstream_missing':upstream_missing,'upstream_ports':upstream_ports}))",
    ))
    config = f"{prefix} --profile '*' config --format json"
    return shlex.join(["python3", "-c", program, config, capture_service or "", SERVICE_NAME,
                       json.dumps(sorted(upstreams)), str(int(gate_port)) if gate_port else ""])


def check_preflight(output: object) -> None:
    try:
        observed = json.loads(str(output))
        present, capture_ports, login_ports = (
            observed["capture_present"], observed["capture_ports"], observed["login_ports"])
    except (ValueError, KeyError, TypeError):
        raise LoginServiceError("login_service_preflight_failed", "the Compose topology could not be read") from None
    if not present:
        raise LoginServiceError("capture_not_in_project", "the capture service is not in this Compose project")
    if capture_ports:
        raise LoginServiceError("capture_port_published", "the capture service may publish ports only on loopback")
    upstream_missing, upstream_ports = observed.get("upstream_missing", 0), observed.get("upstream_ports", 0)
    if not isinstance(upstream_missing, int) or not isinstance(upstream_ports, int):
        raise LoginServiceError("login_service_preflight_failed", "the Compose topology could not be read")
    if upstream_missing:
        raise LoginServiceError("upstream_not_in_project", "a site_gate upstream is not in this Compose project")
    if upstream_ports:
        raise LoginServiceError("upstream_port_published",
                                "a gated service (a site_gate upstream, or the routed service of a private "
                                "target) may publish ports only on loopback")
    if login_ports:
        raise LoginServiceError("login_port_published", f"{SERVICE_NAME} must not publish a port")


def render_token_record(entry: str, token_hash: str, issued_at: int, expires_at: int,
                        email: str | None = None) -> str:
    record = {"schema": RECORD_SCHEMA, "entry": entry, "token_sha256": token_hash,
              "issued_at": int(issued_at), "expires_at": int(expires_at)}
    if email is not None:
        record["email"] = email
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def record_path(entry: str) -> str:
    return f"{STATE_DIRECTORY}/{entry}.json"


def install_script(target: str) -> str:
    """Write stdin to ``target`` so a reader never sees a partial record."""
    quoted = shlex.quote(target)
    return (f"set -eu; umask 077; tmp={quoted}.tmp.$$; trap 'rm -f \"$tmp\"' EXIT; "
            f"cat > \"$tmp\"; mv -f \"$tmp\" {quoted}; trap - EXIT")


def valid_revoke_target(value: object) -> bool:
    """One link (short or old-form id), one session, or ``all``: the share module's rule."""
    from sandbox.hosting import share
    return isinstance(value, str) and bool(share.REVOKE_TARGET_RE.fullmatch(value))


def site_command(prefix: str, verb: str, entry: str, *extra: str) -> str:
    """Run the service's runtime to mint, list, revoke, or check capabilities.

    The argv holds the public runtime source and ids only. A mint record goes on stdin.
    """
    if verb not in {"mint", "list", "revoke", "capabilities"}:
        raise LoginServiceError("site_usage", "unsupported site command")
    if not isinstance(entry, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", entry):
        raise LoginServiceError("site_usage", "invalid site entry")
    if (verb == "revoke" and (len(extra) != 1 or not valid_revoke_target(extra[0]))) or (verb != "revoke" and extra):
        raise LoginServiceError("site_usage", "invalid site command arguments")
    return (f"{prefix} exec -T {SERVICE_NAME} "
            + shlex.join(["python3", "-c", _LOADER, _runtime_source(), "site", verb, entry, *extra]))


def parse_site_reply(output: object) -> dict:
    """Read the one JSON line a site command prints, refusing anything else."""
    lines = [line for line in str(output or "").splitlines() if line.strip()]
    try:
        reply = json.loads(lines[-1])
    except (IndexError, ValueError):
        raise LoginServiceError("login_link_failed", "the login service gave no readable answer") from None
    if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
        raise LoginServiceError("login_link_failed", "the login service gave no readable answer")
    if not reply["ok"]:
        code = reply.get("code")
        code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,40}", code) else "login_link_failed"
        raise LoginServiceError(code, _SITE_REFUSALS.get(code, "the login service refused the request"))
    return reply


_SITE_REFUSALS = {
    "site_link_limit": "this entry already has the maximum number of live share links; revoke some first",
    "site_record_limit": "this entry keeps the maximum number of share links, spent ones included, "
                         "until they expire; revoke spent links (or all) first",
    "site_link_unknown": "no live share link or session has that id",
    "site_entry_unknown": "the running login service does not know this site_gate entry; run `./sb host apply` first",
    "login_service_expired": "autologin.login_service.enabled_until has passed",
}


def login_link(entry: dict, token: str) -> str:
    """The token sits in the fragment, so no request line or proxy log ever holds it."""
    return f"https://{entry['hostname']}{entry['path']}#{LINK_PARAMETER}={token}"


def validate_email(value: object) -> str:
    address = str(value or "").strip()
    if len(address) > 254 or not _EMAIL.fullmatch(address):
        raise LoginServiceError("invalid_email", "the email address is not valid")
    return address


@dataclass(frozen=True)
class ResolvedEmail:
    address: str
    source: str  # argument | saved_default | prompt
    replace_default: bool = False


def _permitted(config: dict, chosen: ResolvedEmail) -> ResolvedEmail:
    denied = (config.get("magic_link") or {}).get("denied_emails") or ()
    if chosen.address.casefold() in denied:
        raise LoginServiceError("email_denied", "this address may not use this login entry")
    return chosen


def resolve_email(config: dict, *, requested: str | None, replace_default: bool, saved: str | None,
                  stdin, stderr) -> ResolvedEmail | None:
    """Choose the address for one link. Never guesses: no git config, no account."""
    if not config["requires_email"]:
        if requested is not None or replace_default:
            raise LoginServiceError("email_not_applicable", "this login entry does not use an email")
        return None
    if requested is not None:
        return _permitted(config, ResolvedEmail(validate_email(requested), "argument", replace_default))
    if replace_default:
        raise LoginServiceError("email_required", "--save-email needs --email with the address to save")
    if saved:
        return _permitted(config, ResolvedEmail(validate_email(saved), "saved_default"))
    if getattr(stdin, "isatty", lambda: False)():
        stderr.write("Email to sign in as: ")
        stderr.flush()
        return _permitted(config, ResolvedEmail(validate_email(stdin.readline()), "prompt"))
    raise LoginServiceError(
        "email_required", "no saved email for this target; ask the person which email to use, then pass --email")
