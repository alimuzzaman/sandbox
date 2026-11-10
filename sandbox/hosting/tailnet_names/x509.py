"""Minimal, read-only DER reading of an X.509 leaf (spec 078 hardening).

The probes run with verification off (the controller's CA bundle is not
trusted to exist), so the presented leaf is decoded here to learn which names
it covers and when it expires. Any malformed input raises ``ValueError``;
callers treat that as inconclusive, never as "does not cover the name".
"""
from __future__ import annotations

import datetime as dt
import ssl
from dataclasses import dataclass

_SAN_OID = bytes.fromhex("551d11")   # 2.5.29.17 subjectAltName
_CN_OID = bytes.fromhex("550403")    # 2.5.4.3 commonName
_SEQUENCE, _SET, _EXPLICIT_0, _EXPLICIT_3 = 0x30, 0x31, 0xA0, 0xA3
_DNS_NAME = 0x82
_UTC_TIME, _GENERALIZED_TIME = 0x17, 0x18


def _read(buf: bytes, i: int) -> tuple[int, bytes, int]:
    if i + 2 > len(buf):
        raise ValueError("truncated DER")
    tag, length = buf[i], buf[i + 1]
    i += 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or i + count > len(buf):
            raise ValueError("bad DER length")
        length = int.from_bytes(buf[i:i + count], "big")
        i += count
    end = i + length
    if end > len(buf):
        raise ValueError("truncated DER")
    return tag, buf[i:end], end


def _children(buf: bytes) -> list[tuple[int, bytes]]:
    out, i = [], 0
    while i < len(buf):
        tag, value, i = _read(buf, i)
        out.append((tag, value))
    return out


def _only(buf: bytes, tag: int) -> bytes:
    found_tag, value, end = _read(buf, 0)
    if found_tag != tag or end != len(buf):
        raise ValueError("unexpected DER structure")
    return value


def _time(tag: int, value: bytes) -> dt.datetime:
    text = value.decode("ascii")
    if tag == _UTC_TIME and len(text) == 13 and text.endswith("Z"):
        year = int(text[:2])
        text = ("19" if year >= 50 else "20") + text
    elif not (tag == _GENERALIZED_TIME and len(text) == 15 and text.endswith("Z")):
        raise ValueError("unsupported certificate time")
    return dt.datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=dt.timezone.utc)


@dataclass(frozen=True)
class Leaf:
    names: tuple[str, ...]
    not_after: dt.datetime


def parse_leaf(der: bytes) -> Leaf:
    certificate = _children(_only(der, _SEQUENCE))
    if not certificate or certificate[0][0] != _SEQUENCE:
        raise ValueError("no TBSCertificate")
    fields = _children(certificate[0][1])
    start = 1 if fields and fields[0][0] == _EXPLICIT_0 else 0
    if len(fields) < start + 6:
        raise ValueError("short TBSCertificate")
    validity, subject = fields[start + 3], fields[start + 4]
    times = _children(validity[1])
    if validity[0] != _SEQUENCE or len(times) != 2:
        raise ValueError("bad validity")
    not_after = _time(*times[1])
    common: list[str] = []
    dns: list[str] = []
    for set_tag, rdn in _children(subject[1]):
        if set_tag != _SET:
            raise ValueError("bad subject")
        for _seq, attribute in _children(rdn):
            parts = _children(attribute)
            if len(parts) == 2 and parts[0][1] == _CN_OID:
                common.append(parts[1][1].decode("utf-8", "replace").lower())
    for tag, value in fields[start + 6:]:
        if tag != _EXPLICIT_3:
            continue
        for _seq, extension in _children(_only(value, _SEQUENCE)):
            parts = _children(extension)
            if parts and parts[0][1] == _SAN_OID:
                for name_tag, name in _children(_only(parts[-1][1], _SEQUENCE)):
                    if name_tag == _DNS_NAME:
                        dns.append(name.decode("ascii").lower())
    # DNS SANs take precedence; the subject CN counts only without them
    # (RFC 6125 section 6.4.4).
    return Leaf(tuple(dict.fromkeys(dns or common)), not_after)


def first_der(chain_pem: str) -> bytes:
    """The leaf (first certificate) of a PEM chain, as DER."""
    begin, end = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
    start = chain_pem.find(begin)
    stop = chain_pem.find(end, start)
    if start < 0 or stop < 0:
        raise ValueError("no certificate in the chain")
    return ssl.PEM_cert_to_DER_cert(chain_pem[start:stop + len(end)] + "\n")


def covers(names: tuple[str, ...], fqdn: str) -> bool:
    """True when a certificate with ``names`` is valid for ``fqdn`` (RFC 6125)."""
    fqdn = fqdn.lower()
    parent = fqdn.split(".", 1)[1] if "." in fqdn else None
    for name in (item.lower() for item in names):
        if name == fqdn:
            return True
        if name.startswith("*.") and parent is not None and name[2:] == parent:
            return True
    return False
