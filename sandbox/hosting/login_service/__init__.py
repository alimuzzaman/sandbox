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
ENTRY_MODES = ("magic_link", "inbox_gate")

_ENTRY_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
_PINNED_IMAGE = re.compile(r"[a-z0-9][a-z0-9./_:-]*@sha256:[0-9a-f]{64}")
_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)
_SERVICE_FIELDS = {"image", "enabled_until", "session_ttl_seconds", "allowed_host_suffixes", "capture"}
_CAPTURE_FIELDS = {"service", "adapter", "api_url", "username", "credential_secret"}
_ENTRY_FIELDS = {"mode", "requires_email", "hostname", "path", "ttl_seconds", "magic_link", "landing_path"}
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
    if not path.startswith("/") or "?" in path or "#" in path or ".." in path.split("/"):
        raise _invalid(f"{where} must be an absolute URL path without a query or fragment")
    return path


def _origin(value: object, where: str) -> str:
    text = str(value or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        raise _invalid(f"{where} must be an https origin with no path")
    return text


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
    username = str(raw.get("username") or "").strip()
    if not username or ":" in username:
        raise _invalid("login_service.capture.username must be a non-empty name without a colon")
    credential = str(raw.get("credential_secret") or "").strip()
    if credential not in {*secrets.get("required", {}), *secrets.get("generated", {})}:
        raise _invalid("login_service.capture.credential_secret must name a required or generated secret")
    return {"service": service, "adapter": raw["adapter"], "api_url": url.geturl(),
            "username": username, "credential_secret": credential}


def _parse_magic_link(raw: object, *, services: set[str], where: str) -> dict:
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
    raw_denied = raw.get("denied_emails", [])
    if not isinstance(raw_denied, list):
        raise _invalid(f"{prefix}.denied_emails must be a list of email addresses")
    try:
        denied = [validate_email(item).casefold() for item in raw_denied]
    except LoginServiceError:
        raise _invalid(f"{prefix}.denied_emails must be a list of email addresses") from None
    return {
        "request_url": request.geturl(),
        "request_origin": _origin(raw.get("request_origin"), f"{prefix}.request_origin"),
        "link_origin": _origin(raw.get("link_origin"), f"{prefix}.link_origin"),
        "link_path": _url_path(raw.get("link_path"), f"{prefix}.link_path"),
        "link_pattern": pattern,
        "required_params": dict(required),
        "denied_emails": denied,
        "poll_timeout_seconds": _bounded_int(
            raw.get("poll_timeout_seconds", 10), 1, MAX_POLL_SECONDS, f"{prefix}.poll_timeout_seconds"),
    }


def _parse_entry(name: object, raw: object, *, gate_hosts: set[str], services: set[str]) -> dict:
    if not isinstance(name, str) or not _ENTRY_NAME.fullmatch(name):
        raise _invalid("entry name must be lowercase letters, digits, or hyphens")
    where = f"entries.{name}"
    if not isinstance(raw, dict):
        raise _invalid(f"{where} must be a mapping")
    _reject_unknown(raw, _ENTRY_FIELDS, where)
    mode = raw.get("mode")
    if mode not in ENTRY_MODES:
        raise _invalid(f"{where}.mode must be one of: " + ", ".join(ENTRY_MODES))
    needs_email = raw.get("requires_email")
    if not isinstance(needs_email, bool) or needs_email is not (mode == "magic_link"):
        raise _invalid(f"{where}.requires_email must be true for magic_link and false for inbox_gate")
    hostname = str(raw.get("hostname") or "").strip().rstrip(".").lower()
    if hostname not in gate_hosts:
        raise _invalid(f"{where}.hostname must be a served alias of this target, not its primary hostname")
    path = _url_path(raw.get("path"), f"{where}.path")
    if path == "/":
        raise _invalid(f"{where}.path must be a dedicated path, not /")
    entry = {
        "mode": mode, "requires_email": needs_email, "hostname": hostname, "path": path,
        "ttl_seconds": _bounded_int(raw.get("ttl_seconds", DEFAULT_LINK_SECONDS), MIN_LINK_SECONDS,
                                    MAX_LINK_SECONDS, f"{where}.ttl_seconds"),
    }
    if mode == "magic_link":
        entry["magic_link"] = _parse_magic_link(raw.get("magic_link"), services=services, where=where)
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
        if not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes):
            raise _invalid(f"allowed_host_suffixes does not cover target hostname {hostname}")
    return suffixes


def parse_declaration(raw: object, *, routes: list[dict], compose: dict, secrets: dict) -> dict | None:
    """Return the normalized declaration, or None when none is declared."""
    if not is_declared(raw):
        return None
    if set(raw) != {"login_service", "entries"}:
        raise _invalid("autologin must hold exactly login_service and entries in this form")
    service = raw["login_service"]
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
    capture = _parse_capture(service.get("capture"), primary=primary, background=background, secrets=secrets)
    entries = raw["entries"]
    if not isinstance(entries, dict) or not entries:
        raise _invalid("entries must be a non-empty mapping")
    gate_hosts = {route["hostname"] for route in routes
                  if route["mode"] == "serve" and not route.get("primary")
                  and not route["hostname"].startswith("*.")}
    callable_services = {primary, *background} - {capture["service"]}
    parsed = {name: _parse_entry(name, item, gate_hosts=gate_hosts, services=callable_services)
              for name, item in entries.items()}
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
    return {"image": image, "enabled_until": enabled_until.isoformat(),
            "session_ttl_seconds": session, "allowed_host_suffixes": suffixes,
            "capture": capture, "entries": parsed}


def _enabled_until_epoch(declaration: dict) -> int:
    """The declared date counts through its end, UTC."""
    date = parse_enabled_until(declaration["enabled_until"])
    end = dt.datetime(date.year, date.month, date.day, tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
    return int(end.timestamp())


def assert_applicable(declaration: dict, *, now: float) -> None:
    if now >= _enabled_until_epoch(declaration):
        raise LoginServiceError("login_service_expired", "autologin.login_service.enabled_until has passed")
    today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    if (parse_enabled_until(declaration["enabled_until"]) - today).days > MAX_ENABLED_DAYS:
        raise LoginServiceError(
            "login_service_expiry_too_far",
            f"autologin.login_service.enabled_until must be within {MAX_ENABLED_DAYS} days")


def status_view(declaration: dict, *, now: float) -> dict:
    return {
        "state": "expired" if now >= _enabled_until_epoch(declaration) else "enabled",
        "enabled_until": declaration["enabled_until"],
        "entries": [{"name": name, "mode": entry["mode"], "hostname": entry["hostname"],
                     "requires_email": entry["requires_email"]}
                    for name, entry in sorted(declaration["entries"].items())],
    }


def runtime_config(declaration: dict) -> dict:
    """What the running service needs; never carries a secret or its name."""
    capture = declaration["capture"]
    entries = {}
    for name, entry in declaration["entries"].items():
        entries[name] = dict(entry)
        if "magic_link" in entry:
            # Refused at issue, so these addresses never need to reach the server.
            entries[name]["magic_link"] = {
                key: value for key, value in entry["magic_link"].items() if key != "denied_emails"}
    return {
        "enabled_until_epoch": _enabled_until_epoch(declaration),
        "session_ttl_seconds": declaration["session_ttl_seconds"],
        "capture": {key: capture[key] for key in ("adapter", "api_url", "username")},
        "entries": entries,
    }


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def render_service(declaration: dict) -> str:
    """Render the Compose service block; it joins the override's ``services:``."""
    source = _b64(Path(__file__).with_name("runtime.py").read_bytes())
    config = _b64(json.dumps(runtime_config(declaration), sort_keys=True, separators=(",", ":")).encode())
    loader = "import base64,sys;exec(compile(base64.b64decode(sys.argv[1]),'login_service','exec'))"
    probe = f"import urllib.request as u;u.urlopen('http://127.0.0.1:{HEALTH_PORT}/healthz',timeout=3)"
    capture = declaration["capture"]
    lines = [
        f"  {SERVICE_NAME}:",
        f"    image: {json.dumps(declaration['image'])}",
        '    restart: "unless-stopped"',
        '    user: "65534:65534"',
        "    read_only: true",
        "    cap_drop: [\"ALL\"]",
        "    security_opt: [\"no-new-privileges:true\"]",
        f"    tmpfs: [{json.dumps(STATE_DIRECTORY + ':mode=0700,uid=65534,gid=65534,size=1m')}]",
        f"    expose: [\"{INTERNAL_PORT}\"]",
        "    depends_on:",
        f"      {capture['service']}:",
        "        condition: service_started",
        "    environment:",
        f'      SANDBOX_LOGIN_CONFIG: "{config}"',
        f'      SANDBOX_LOGIN_CAPTURE_CREDENTIAL: "${{{capture["credential_secret"]}}}"',
        f"    command: {json.dumps(['python3', '-c', loader, source])}",
        "    healthcheck:",
        f"      test: {json.dumps(['CMD', 'python3', '-c', probe])}",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 12",
    ]
    return "\n".join(lines) + "\n"


def preflight_command(prefix: str, capture_service: str) -> str:
    """A read-only remote probe; its output is counts, never Compose values."""
    program = "\n".join((
        "import json,subprocess,sys",
        "command,capture,login=sys.argv[1:4]",
        "done=subprocess.run(command,shell=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=60,check=True)",
        "services=json.loads(done.stdout)['services']",
        "def exposed(name):",
        " service=services.get(name) or {}",
        " return len(service.get('ports') or [])+(1 if service.get('network_mode')=='host' else 0)",
        "print(json.dumps({'capture_present':capture in services,'capture_ports':exposed(capture),'login_ports':exposed(login)}))",
    ))
    config = f"{prefix} --profile '*' config --format json"
    return shlex.join(["python3", "-c", program, config, capture_service, SERVICE_NAME])


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
        raise LoginServiceError("capture_port_published", "the capture service must not publish a port")
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
