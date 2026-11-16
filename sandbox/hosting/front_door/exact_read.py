"""Read a front-door route file byte for byte through any transport.

The server-local owner channel redacts process output, and a route read back
as text came back changed: ``proxy_pass http://127.0.0.1:18199;`` became
``proxy_pass [REDACTION_FAILED]``, so a rollback reinstalled a route nginx
rejects. The file travels as hex (nothing redaction rewrites) with its SHA-256,
and any change refuses rather than returning altered text.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import re
import shlex

ABSENT_EXIT = 43
NOT_A_FILE_EXIT = 44

SCRIPT = ('if [ -f "$1" ]; then sha256sum < "$1" | cut -c1-64 && '
          'od -An -v -tx1 < "$1" | tr -d " \\n" && echo; '
          'elif [ -e "$1" ] || [ -L "$1" ]; then exit 44; else exit 43; fi')


class ExactReadError(ValueError):
    """The file's bytes did not arrive intact."""


def command(path: str, *, sudo_prefix: str = "") -> str:
    """``sudo_prefix`` defines ``$SUDO`` (the nginx edge reads root-owned files)."""
    runner = "$SUDO sh" if sudo_prefix else "sh"
    return f"{sudo_prefix}{runner} -c {shlex.quote(SCRIPT)} sandbox-read {shlex.quote(path)}"


def decode(stdout) -> str:
    if isinstance(stdout, bytes):
        stdout = stdout.decode("ascii", "replace")
    lines = (stdout or "").strip().split("\n")
    digest = lines[0].strip() if lines else ""
    body = lines[1].strip() if len(lines) > 1 else ""
    if len(lines) > 2 or not re.fullmatch(r"[0-9a-f]{64}", digest) or \
            not re.fullmatch(r"(?:[0-9a-f]{2})*", body):
        raise ExactReadError("route file did not arrive intact")
    data = bytes.fromhex(body)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ExactReadError("route file did not arrive intact")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ExactReadError("route file is not UTF-8 text") from None
