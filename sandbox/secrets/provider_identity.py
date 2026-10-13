"""Canonical non-secret provider identities shared by config and credentials."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


def canonical_https_origin(value: object) -> str:
    """Return one IDNA/lowercase HTTPS origin or raise ``ValueError``.

    This function is deliberately the single canonicalizer used by both config
    normalization and OS-credential locators.  Otherwise equivalent spellings
    could approve one endpoint while authenticating to another identity.
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2_048
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        or "\\" in value
    ):
        raise ValueError("provider endpoint must be a canonical HTTPS origin")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("provider endpoint must be a canonical HTTPS origin") from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or "%" in parsed.netloc
        or parsed.netloc.endswith(":")
    ):
        raise ValueError("provider endpoint must be a canonical HTTPS origin")
    hostname = parsed.hostname
    if not hostname or hostname.endswith("."):
        raise ValueError("provider endpoint must be a canonical HTTPS origin")
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("provider endpoint must be a canonical HTTPS origin") from None
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("provider endpoint must be a canonical HTTPS origin")
    if ":" in host:
        host = f"[{host}]"
    authority = host if port in (None, 443) else f"{host}:{port}"
    return f"https://{authority}"


__all__ = ["canonical_https_origin"]
