"""Types, codes and boundary parsers for tailnet-only hostnames (spec 078).

Every external input (CLI arguments, the ``tailnet_names`` block of
``sandbox.local.yml``, the state file, remote program output and child output)
is parsed here into a frozen type. Code past this module never handles raw
dictionaries from those sources.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field, replace
from typing import Callable, Literal, Mapping
from urllib.parse import urlparse

from sandbox.core._hosting import HostingRefusal


class TailnetNameError(HostingRefusal):
    """A tailnet-name refusal or failure with a stable ``code``."""


# Stable codes (contracts/host-tailnet-name-cli.md).
INVALID = "tailnet_name_invalid"
ZONE_NOT_ALLOWED = "tailnet_name_zone_not_allowed"
UPSTREAM_INVALID = "tailnet_name_upstream_invalid"
CONFIG_MISSING = "tailnet_name_config_missing"
CONFIG_INVALID = "tailnet_name_config_invalid"
REMOTE_UNKNOWN = "tailnet_name_remote_unknown"
NO_TAILNET_ADDRESS = "tailnet_name_no_tailnet_address"
TAILNET_ADDRESS_UNBOUND = "tailnet_name_tailnet_address_unbound"
PUBLIC_ADDRESS_INVALID = "tailnet_name_public_address_invalid"
ACME_CLIENT_MISSING = "tailnet_name_acme_client_missing"
BUSY = "tailnet_name_busy"
GATE_UNAVAILABLE = "tailnet_name_gate_unavailable"
SECRET_UNAVAILABLE = "tailnet_name_secret_unavailable"
DNS_CONFLICT = "tailnet_name_dns_conflict"
DNS_FAILED = "tailnet_name_dns_failed"
DNS_NOT_OWNED = "tailnet_name_dns_not_owned"
UPSTREAM_DIFFERS = "tailnet_name_upstream_differs"
CERTIFICATE_FAILED = "tailnet_name_certificate_failed"
INSTALL_FAILED = "tailnet_name_install_failed"
NGINX_REJECTED = "tailnet_name_nginx_rejected"
RELOAD_FAILED = "tailnet_name_reload_failed"
REMOTE_FAILED = "tailnet_name_remote_failed"
TAILNET_PROBE_FAILED = "tailnet_name_tailnet_probe_failed"
PUBLIC_EXPOSED = "tailnet_name_public_exposed"
PUBLIC_PROBE_UNAVAILABLE = "tailnet_name_public_probe_unavailable"
NOT_FOUND = "tailnet_name_not_found"
NOT_DUE = "tailnet_name_not_due"
PUBLIC_PROBE_INCONCLUSIVE = "tailnet_name_public_probe_inconclusive"
ACME_CLIENT_UNTRUSTED = "tailnet_name_acme_client_untrusted"
STATE_CORRUPT = "tailnet_name_state_corrupt"
UNMANAGED = "tailnet_name_unmanaged"
REMOTE_PATH_UNSAFE = "tailnet_name_remote_path_unsafe"

TAILNET_NETWORK = ipaddress.ip_network("100.64.0.0/10")
UPSTREAM_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"))
RENEWAL_WINDOW_DAYS = 30
DNS_TOKEN_DESTINATION = "CLOUDFLARE_DNS_API_TOKEN"
ROUTE_PREFIX = "tailnet-"
CERT_ROOT = "/etc/nginx/sandbox-tailnet"
# The nginx route name is ``tailnet-<fqdn>`` and route_file() accepts at most
# 151 characters, so a name may be at most 143 characters.
MAX_FQDN_LENGTH = 151 - len(ROUTE_PREFIX)
ACME_SERVERS = {
    "letsencrypt": "https://acme-v02.api.letsencrypt.org/directory",
    "letsencrypt-staging": "https://acme-staging-v02.api.letsencrypt.org/directory",
}

_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ISO_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,189}")


def hostname(value: object) -> str | None:
    """Return ``value`` as a lowercase multi-label DNS name, or None."""
    if not isinstance(value, str) or not value or len(value) > 253:
        return None
    if value != value.lower() or value.endswith("."):
        return None
    labels = value.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        return None
    try:
        if value.encode("idna").decode("ascii") != value:
            return None
    except UnicodeError:
        return None
    return value


def acme_directory(value: object) -> str:
    """Resolve an ACME directory shortcode or ``https://`` URL."""
    if isinstance(value, str) and value in ACME_SERVERS:
        return ACME_SERVERS[value]
    if isinstance(value, str) and not any(ch.isspace() for ch in value):
        parsed = urlparse(value)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username \
                and not parsed.password:
            return value
    raise TailnetNameError(CONFIG_INVALID,
                           "acme_server must be letsencrypt, letsencrypt-staging or an https URL")


@dataclass(frozen=True)
class TailnetNameConfig:
    zones: tuple[str, ...]
    token_source: str
    token_key: str
    acme_email: str
    acme_server: str = ACME_SERVERS["letsencrypt"]
    lego_path: str | None = None
    lego_sha256: str | None = None

    @classmethod
    def parse(cls, block: object) -> "TailnetNameConfig":
        if block is None:
            raise TailnetNameError(
                CONFIG_MISSING,
                "add a tailnet_names block (zones, cloudflare_token, acme_email) "
                "to $SANDBOX_HOME/sandbox.local.yml")
        if not isinstance(block, Mapping):
            raise TailnetNameError(CONFIG_INVALID, "tailnet_names must be a mapping")
        raw_zones = block.get("zones")
        if not isinstance(raw_zones, (list, tuple)) or not raw_zones:
            raise TailnetNameError(CONFIG_INVALID, "tailnet_names.zones must list at least one zone")
        zones: list[str] = []
        for item in raw_zones:
            zone = hostname(item)
            if zone is None:
                raise TailnetNameError(CONFIG_INVALID, "tailnet_names.zones has an invalid zone name")
            if zone not in zones:
                zones.append(zone)
        token = block.get("cloudflare_token")
        if not isinstance(token, Mapping):
            raise TailnetNameError(
                CONFIG_INVALID,
                "tailnet_names.cloudflare_token must name a broker source and key")
        source, key = token.get("source"), token.get("key")
        if not (isinstance(source, str) and _REFERENCE.fullmatch(source)
                and isinstance(key, str) and _REFERENCE.fullmatch(key)):
            raise TailnetNameError(
                CONFIG_INVALID,
                "tailnet_names.cloudflare_token needs source and key names, never a value")
        if set(token) - {"source", "key"}:
            raise TailnetNameError(
                CONFIG_INVALID,
                "tailnet_names.cloudflare_token takes only source and key")
        email = block.get("acme_email")
        if not isinstance(email, str) or len(email) > 254 or not _EMAIL.fullmatch(email):
            raise TailnetNameError(CONFIG_INVALID, "tailnet_names.acme_email must be an email address")
        server = acme_directory(block.get("acme_server", "letsencrypt"))
        lego = block.get("lego")
        if lego is not None and not (isinstance(lego, str) and lego.startswith("/")
                                     and "\x00" not in lego):
            raise TailnetNameError(CONFIG_INVALID, "tailnet_names.lego must be an absolute path")
        digest = block.get("lego_sha256")
        if digest is not None and not (isinstance(digest, str) and _HEX64.fullmatch(digest)):
            raise TailnetNameError(CONFIG_INVALID,
                                   "tailnet_names.lego_sha256 must be 64 lowercase hex digits")
        unknown = set(block) - {"zones", "cloudflare_token", "acme_email", "acme_server", "lego",
                                "lego_sha256"}
        if unknown:
            raise TailnetNameError(CONFIG_INVALID,
                                   "tailnet_names has unknown keys: " + ", ".join(sorted(unknown)))
        return cls(tuple(zones), source, key, email, server, lego, digest)

    @classmethod
    def load(cls) -> "TailnetNameConfig":
        from sandbox.core._config import _local_yaml
        document = _local_yaml() or {}
        return cls.parse(document.get("tailnet_names") if isinstance(document, Mapping) else None)

    def with_acme_server(self, value: str | None) -> "TailnetNameConfig":
        return self if value is None else replace(self, acme_server=acme_directory(value))


@dataclass(frozen=True)
class TailnetName:
    fqdn: str
    zone: str

    @property
    def route_name(self) -> str:
        return ROUTE_PREFIX + self.fqdn

    @property
    def cert_dir(self) -> str:
        return f"{CERT_ROOT}/{self.fqdn}"

    @classmethod
    def parse(cls, value: object, zones: tuple[str, ...]) -> "TailnetName":
        if isinstance(value, str) and "*" in value:
            raise TailnetNameError(INVALID, "wildcard names are not supported")
        fqdn = hostname(value)
        if fqdn is None:
            raise TailnetNameError(INVALID, "the name must be a lowercase DNS hostname")
        if len(fqdn) > MAX_FQDN_LENGTH:
            raise TailnetNameError(INVALID, f"the name must be at most {MAX_FQDN_LENGTH} characters")
        if fqdn in zones:
            raise TailnetNameError(INVALID, "the zone apex cannot be a tailnet name")
        matches = [zone for zone in zones if fqdn.endswith("." + zone)]
        if not matches:
            raise TailnetNameError(ZONE_NOT_ALLOWED,
                                   "the name is not under a zone listed in tailnet_names.zones")
        return cls(fqdn, max(matches, key=len))


@dataclass(frozen=True)
class Upstream:
    host: str
    port: int

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"

    @classmethod
    def parse(cls, value: object) -> "Upstream":
        if not isinstance(value, str) or value.count(":") != 1:
            raise TailnetNameError(UPSTREAM_INVALID, "the upstream must be HOST:PORT")
        host, port_text = value.split(":")
        if host == "localhost":
            host = "127.0.0.1"
        try:
            address = ipaddress.IPv4Address(host)
        except ValueError:
            raise TailnetNameError(
                UPSTREAM_INVALID, "the upstream host must be an IPv4 address or localhost") from None
        if not any(address in net for net in UPSTREAM_NETWORKS):
            raise TailnetNameError(
                UPSTREAM_INVALID,
                "the upstream must be a loopback, private or tailnet address")
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise TailnetNameError(UPSTREAM_INVALID, "the upstream port must be 1-65535")
        return cls(str(address), int(port_text))


def tailnet_address(value: object) -> str:
    try:
        address = ipaddress.IPv4Address(value) if isinstance(value, str) else None
    except ValueError:
        address = None
    if address is None or address not in TAILNET_NETWORK:
        raise TailnetNameError(
            NO_TAILNET_ADDRESS,
            "the remote has no recorded tailnet IPv4 (tailscale_host); "
            "record it with `sb remote join`")
    return str(address)


# Addresses that can never be the remote's public side. Documentation ranges
# (203.0.113.0/24 and friends) are deliberately not listed: Python's
# ``is_global`` rejects them, but they stand in for public addresses in tests.
_NOT_PUBLIC = tuple(ipaddress.ip_network(net) for net in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4"))


def _global_ipv4(value: str) -> str | None:
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        return None
    return None if any(address in net for net in _NOT_PUBLIC) else str(address)


def public_address(*, override: str | None, ssh_host: str | None,
                   resolver: Callable = socket.getaddrinfo) -> str | None:
    """The address the exposure check probes: an explicit override or the SSH host."""
    if override is not None:
        address = _global_ipv4(override)
        if address is None:
            raise TailnetNameError(PUBLIC_ADDRESS_INVALID,
                                   "--public-address must be a public IPv4 address")
        return address
    if not ssh_host:
        return None
    direct = _global_ipv4(ssh_host)
    if direct is not None:
        return direct
    try:
        rows = resolver(ssh_host, 443, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return None
    for row in rows:
        candidate = _global_ipv4(row[4][0])
        if candidate is not None:
            return candidate
    return None


@dataclass(frozen=True)
class RemoteTarget:
    name: str
    entry: Mapping
    tailnet_ip: str
    public_ip: str | None


StepName = Literal["preflight", "gate", "dns", "certificate", "activate",
                   "verify_tailnet", "verify_public", "remove_block", "remove_certificate",
                   "remove_dns"]
StepStatus = Literal["done", "skipped", "refused", "failed", "not_run"]


@dataclass(frozen=True)
class StepOutcome:
    step: StepName
    status: StepStatus
    code: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        out = {"step": self.step, "status": self.status}
        if self.code:
            out["code"] = self.code
        if self.detail:
            out["detail"] = self.detail
        return out


def _optional_str(row: Mapping, key: str) -> str | None:
    value = row.get(key)
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class RemoteInventoryEntry:
    fqdn: str
    block_present: bool
    header: Mapping | None
    cert_present: bool
    key_present: bool
    cert_not_after: str | None
    cert_sha256: str | None
    cert_names: tuple[str, ...]
    key_matches: bool | None
    block_managed: bool = False
    dir_present: bool = False
    dir_managed: bool = False
    dir_unsafe: bool = False
    activation: str | None = None

    @property
    def upstream(self) -> str | None:
        return _optional_str(self.header or {}, "upstream")

    @classmethod
    def parse(cls, row: object) -> "RemoteInventoryEntry":
        if not isinstance(row, Mapping) or hostname(row.get("fqdn")) is None:
            raise TailnetNameError(REMOTE_FAILED, "the remote returned an invalid inventory row")
        header = row.get("header")
        if header is not None and not (isinstance(header, Mapping)
                                       and header.get("fqdn") == row["fqdn"]):
            header = None
        names = row.get("cert_names") or []
        key_matches = row.get("key_matches")
        return cls(
            fqdn=row["fqdn"],
            block_present=row.get("block_present") is True,
            header=dict(header) if header else None,
            cert_present=row.get("cert_present") is True,
            key_present=row.get("key_present") is True,
            cert_not_after=_optional_str(row, "cert_not_after"),
            cert_sha256=_optional_str(row, "cert_sha256"),
            cert_names=tuple(n for n in names if isinstance(n, str)),
            key_matches=key_matches if isinstance(key_matches, bool) else None,
            block_managed=row.get("block_managed") is True,
            dir_present=row.get("dir_present") is True or row.get("dir_managed") is True
            or row.get("dir_unsafe") is True,
            dir_managed=row.get("dir_managed") is True,
            dir_unsafe=row.get("dir_unsafe") is True,
            activation=_optional_str(row, "activation"),
        )


DnsResultKind = Literal["created", "exists", "absent", "deleted", "conflict", "error"]


@dataclass(frozen=True)
class DnsResult:
    result: DnsResultKind
    record_id: str | None = None
    content: str | None = None
    proxied: bool | None = None
    code: str | None = None
    detail: str | None = None

    @classmethod
    def parse(cls, row: object) -> "DnsResult":
        kinds = ("created", "exists", "absent", "deleted", "conflict", "error")
        if not isinstance(row, Mapping) or row.get("result") not in kinds:
            raise TailnetNameError(DNS_FAILED, "the DNS helper returned no result")
        proxied = row.get("proxied")
        return cls(row["result"], _optional_str(row, "record_id"), _optional_str(row, "content"),
                   proxied if isinstance(proxied, bool) else None,
                   _optional_str(row, "code"), _optional_str(row, "detail"))


RecordState = Literal["publishing", "published", "removing"]


@dataclass(frozen=True)
class TailnetNameRecord:
    fqdn: str
    zone: str
    upstream: str
    tailnet_ip: str
    state: RecordState = "publishing"
    dns_record_id: str | None = None
    dns_adopted: bool = False
    cert_not_after: str | None = None
    cert_sha256: str | None = None
    steps: Mapping[str, str] = field(default_factory=dict)
    updated_at: str = ""
    lease_token: str | None = None

    def as_dict(self) -> dict:
        return {"fqdn": self.fqdn, "zone": self.zone, "upstream": self.upstream,
                "tailnet_ip": self.tailnet_ip, "state": self.state,
                "dns_record_id": self.dns_record_id, "dns_adopted": self.dns_adopted,
                "cert_not_after": self.cert_not_after, "cert_sha256": self.cert_sha256,
                "steps": dict(self.steps), "updated_at": self.updated_at,
                "lease_token": self.lease_token}

    @classmethod
    def parse(cls, row: object) -> "TailnetNameRecord":
        """Parse one stored record strictly; anything malformed is STATE_CORRUPT.

        A record carries DNS ownership, so it is never dropped silently.
        """
        def corrupt(why: str) -> TailnetNameError:
            return TailnetNameError(STATE_CORRUPT, f"a stored tailnet-name record is invalid ({why}); "
                                    "inspect $SANDBOX_HOME/runtime/tailnet-names/ before retrying")
        if not isinstance(row, Mapping):
            raise corrupt("not an object")
        fqdn, zone = hostname(row.get("fqdn")), hostname(row.get("zone"))
        if fqdn is None or zone is None or not fqdn.endswith("." + zone):
            raise corrupt("name or zone")
        if row.get("state") not in ("publishing", "published", "removing"):
            raise corrupt("state")
        try:
            upstream = str(Upstream.parse(row.get("upstream")))
            tailnet_ip = tailnet_address(row.get("tailnet_ip"))
        except TailnetNameError:
            raise corrupt("upstream or tailnet address") from None
        if upstream != row.get("upstream"):
            raise corrupt("upstream")
        for key, pattern in (("dns_record_id", _HEX32), ("cert_sha256", _HEX64),
                             ("lease_token", _HEX32), ("cert_not_after", _ISO_TIME)):
            value = row.get(key)
            if value is not None and not (isinstance(value, str) and pattern.fullmatch(value)):
                raise corrupt(key)
        steps = row.get("steps", {})
        if not isinstance(steps, Mapping) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in steps.items()):
            raise corrupt("steps")
        updated = row.get("updated_at", "")
        if not isinstance(updated, str) or (updated and not _ISO_TIME.fullmatch(updated)):
            raise corrupt("updated_at")
        return cls(
            fqdn=fqdn, zone=zone, upstream=upstream, tailnet_ip=tailnet_ip, state=row["state"],
            dns_record_id=row.get("dns_record_id"), dns_adopted=row.get("dns_adopted") is True,
            cert_not_after=row.get("cert_not_after"), cert_sha256=row.get("cert_sha256"),
            steps=dict(steps), updated_at=updated, lease_token=row.get("lease_token"),
        )
