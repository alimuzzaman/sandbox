"""Metadata-only source continuity snapshots (research R8).

Continuity binds an approval to filesystem metadata plus a hash of entry
*names*. No value, value hash or opaque revision contributes. A value-only edit
that restores mtime still advances ctime, which is why coarse or untrusted
timestamps make a snapshot unreliable rather than silently weaker.
"""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import os
import stat

from .models import SourceContinuity, canonical_json


_NS_PER_SECOND = 1_000_000_000
_FUTURE_CTIME_TOLERANCE_NS = 5 * _NS_PER_SECOND


def _names_sha256(names: Iterable[str]) -> str:
    return hashlib.sha256(canonical_json(sorted(set(names)))).hexdigest()


def _unreliable_reason(info: os.stat_result, now_ns: int) -> str | None:
    if not stat.S_ISREG(info.st_mode):
        return "not_regular_file"
    if info.st_nlink != 1:
        return "unexpected_links"
    if info.st_uid != os.geteuid():
        return "foreign_owner"
    if stat.S_IMODE(info.st_mode) & 0o077:
        return "permissions_too_broad"
    if info.st_ctime_ns > now_ns + _FUTURE_CTIME_TOLERANCE_NS:
        return "ctime_in_future"
    if info.st_mtime_ns % _NS_PER_SECOND == 0 and info.st_ctime_ns % _NS_PER_SECOND == 0:
        return "coarse_timestamps"
    return None


def capture(alias: str, info: os.stat_result, names: Iterable[str], *,
            now_ns: int) -> SourceContinuity:
    """Build a continuity snapshot from one ``fstat`` and the entry names."""
    reason = _unreliable_reason(info, now_ns)
    return SourceContinuity(
        alias=alias,
        dev=info.st_dev,
        ino=info.st_ino,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
        nlink=info.st_nlink,
        uid=info.st_uid,
        mode=info.st_mode,
        names_sha256=_names_sha256(names),
        reliable=reason is None,
        unreliable_reason=reason,
    )


__all__ = ["capture"]
