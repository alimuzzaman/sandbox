"""Untrusted URL selection and exact receiver-observation binding.

Parsing produces a selector only. It does not prove that a hostname or address is
owned by a tailnet node, that a receiver can reach it, or that sharing is authorized.
"""
from __future__ import annotations

import ipaddress
import math
import re
import urllib.parse
from dataclasses import dataclass, field

from sandbox.hosting.share import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MAX_USES,
    MIN_TTL_SECONDS,
    ShareRefusal,
    unsafe_path,
    validate_scope,
)

MAX_URL_BYTES = 4096
MAX_NODE_ADDRESSES = 4
URL_DEFAULT_USES = 1
URL_MIN_TTL_SECONDS = MIN_TTL_SECONDS
URL_DEFAULT_TTL_SECONDS = DEFAULT_TTL_SECONDS
URL_MAX_TTL_SECONDS = MAX_TTL_SECONDS
URL_MAX_USES = MAX_USES

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_NUMERIC_LABEL = re.compile(r"(?:[0-9]+|0[xX][0-9a-fA-F]+)\Z")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_URL_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,=:@%/-]*\Z")
_PERCENT_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")


@dataclass(frozen=True, slots=True)
class URLSelection:
    """Canonical, unverified HTTP(S) selector; query is private input data."""

    scheme: str
    host: str
    port: int
    path: str
    scope: str
    original_authority: str
    query: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReceiverSnapshot:
    """Normalized receiver identity from a trusted observer; not actor authority."""

    receiver_id: str
    observed_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.receiver_id, str) or not self.receiver_id \
                or isinstance(self.observed_at, bool) or not isinstance(self.observed_at, (int, float)) \
                or not math.isfinite(self.observed_at):
            raise _refuse()


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    """One observed node identity and its node-owned Tailscale addresses."""

    stable_id: str
    dns_name: str
    addresses: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.stable_id, str) or not self.stable_id \
                or not isinstance(self.dns_name, str):
            raise _refuse()
        if not isinstance(self.addresses, (tuple, list)):
            raise _refuse()
        try:
            dns_name = _canonical_host(self.dns_name)[0]
            addresses = tuple(_canonical_address(address) for address in self.addresses)
        except (ShareRefusal, TypeError):
            raise _refuse() from None
        object.__setattr__(self, "dns_name", dns_name)
        object.__setattr__(self, "addresses", addresses)


@dataclass(frozen=True, slots=True)
class AddressBinding:
    """Exact observed URL-to-node match; does not imply readiness or authorization."""

    receiver_id: str
    node_id: str
    dns_name: str
    addresses: tuple[str, ...]
    scheme: str
    port: int
    original_authority: str


def _refuse() -> ShareRefusal:
    """Return a fixed message that cannot disclose selector content."""
    return ShareRefusal("share_url_invalid", "URL target is invalid or unsupported")


def _canonical_host(raw_host: str) -> tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address | None]:
    if not raw_host or "%" in raw_host:
        raise _refuse()
    try:
        literal = ipaddress.ip_address(raw_host)
    except ValueError:
        literal = None
    if literal is not None:
        allowed = literal in (_CGNAT if literal.version == 4 else _TAILNET_V6)
        if not allowed:
            raise _refuse()
        return literal.compressed.lower(), literal

    try:
        ascii_host = raw_host.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, ValueError):
        raise _refuse() from None
    if not ascii_host or len(ascii_host) > 253:
        raise _refuse()
    labels = ascii_host.split(".")
    if any(not _DNS_LABEL.fullmatch(label) for label in labels):
        raise _refuse()
    # Numeric-only / legacy numeric forms can be interpreted as IP addresses by
    # clients and resolvers even when a strict IPv4 parser rejects their spelling.
    numeric_labels = tuple(bool(_NUMERIC_LABEL.fullmatch(label)) for label in labels)
    if all(numeric_labels) or (len(labels) >= 5 and all(numeric_labels[:4])):
        raise _refuse()
    return ascii_host, None


def parse_url(value: object, *, scope: object = None) -> URLSelection:
    """Parse a sensitive URL into an immutable selector, never into authority."""
    if not isinstance(value, str):
        raise _refuse()
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        raise _refuse() from None
    if not encoded or len(encoded) > MAX_URL_BYTES:
        raise _refuse()
    if any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value):
        raise _refuse()
    if "\\" in value or "#" in value:
        raise _refuse()
    try:
        parsed = urllib.parse.urlsplit(value)
    except (ValueError, UnicodeError):
        raise _refuse() from None
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https") or not parsed.netloc:
        raise _refuse()
    authority = parsed.netloc
    if "@" in authority or "%" in authority:
        raise _refuse()
    if authority.startswith("["):
        close = authority.find("]")
        if close < 0 or close + 1 >= len(authority) or authority[close + 1] != ":":
            raise _refuse()
        raw_host = authority[1:close]
        raw_port = authority[close + 2:]
        if "]" in raw_port or "[" in raw_host:
            raise _refuse()
    else:
        if authority.count(":") != 1:
            raise _refuse()
        raw_host, raw_port = authority.rsplit(":", 1)
    if not raw_port or not raw_port.isascii() or not raw_port.isdecimal():
        raise _refuse()
    try:
        port = int(raw_port, 10)
        parsed_port = parsed.port
        raw_hostname = parsed.hostname
    except (ValueError, UnicodeError):
        raise _refuse() from None
    if not 1 <= port <= 65535 or parsed_port != port or raw_hostname is None:
        raise _refuse()
    host, literal = _canonical_host(raw_host)
    if literal is not None:
        # A private subnet address is still only a selector. Binding below must
        # prove the exact address is node-owned; subnet routes never suffice.
        pass
    path = parsed.path or "/"
    if not _URL_PATH.fullmatch(path) or _PERCENT_ESCAPE.search(path) or unsafe_path(path):
        raise _refuse()
    try:
        canonical_scope = validate_scope(scope)
    except ShareRefusal:
        raise _refuse() from None
    return URLSelection(scheme, host, port, path, canonical_scope, authority, parsed.query)


def _canonical_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except (TypeError, ValueError):
        raise _refuse() from None
    if address not in (_CGNAT if address.version == 4 else _TAILNET_V6):
        raise _refuse()
    return address.compressed.lower()


def bind_node(
    selection: URLSelection,
    receiver: ReceiverSnapshot,
    node: NodeSnapshot,
    resolved_addresses: tuple[str, ...],
) -> AddressBinding:
    """Bind a selection only to one bounded, exact observed node/address set.

    The caller must obtain these snapshots from its trusted owner-side observer;
    this pure function provides equality and address-class checks only.
    """
    if not isinstance(selection, URLSelection) or not isinstance(receiver, ReceiverSnapshot) \
            or not isinstance(node, NodeSnapshot):
        raise _refuse()
    if not receiver.receiver_id or not node.stable_id or not node.dns_name:
        raise _refuse()
    if not isinstance(resolved_addresses, tuple) or not 1 <= len(resolved_addresses) <= MAX_NODE_ADDRESSES:
        raise _refuse()
    if len(set(resolved_addresses)) != len(resolved_addresses):
        raise _refuse()
    try:
        node_dns = _canonical_host(node.dns_name)[0]
        node_addresses = tuple(_canonical_address(address) for address in node.addresses)
        resolved = tuple(_canonical_address(address) for address in resolved_addresses)
    except (ShareRefusal, TypeError):
        raise _refuse() from None
    if not node_addresses or len(node_addresses) > MAX_NODE_ADDRESSES:
        raise _refuse()
    if len(set(node_addresses)) != len(node_addresses) or set(resolved) != set(node_addresses):
        raise _refuse()
    if selection.host != node_dns and selection.host not in node_addresses:
        raise _refuse()
    if selection.host in node_addresses and selection.host not in resolved:
        raise _refuse()
    return AddressBinding(
        receiver_id=receiver.receiver_id,
        node_id=node.stable_id,
        dns_name=node_dns,
        addresses=tuple(sorted(node_addresses)),
        scheme=selection.scheme,
        port=selection.port,
        original_authority=selection.original_authority,
    )
