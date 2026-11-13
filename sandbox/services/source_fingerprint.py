"""Canonical dirty-overlay fingerprints, including the empty overlay."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable


def dirty_overlay_fingerprint(deleted: Iterable[str], archive: bytes) -> str:
    """Preserve the existing v2 byte contract and request replay identities."""
    digest = hashlib.sha256(b'sandbox-dirty-overlay-v2\0')
    for relative in sorted(deleted):
        encoded = relative.encode('utf-8', 'surrogateescape')
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
    digest.update(hashlib.sha256(archive).digest())
    return digest.hexdigest()


EMPTY_OVERLAY_FINGERPRINT = dirty_overlay_fingerprint((), b'')


def source_has_changes(fingerprint: str | None) -> bool:
    """A retained empty fingerprint is evidence of clean input, not no evidence."""
    return fingerprint not in (None, '', EMPTY_OVERLAY_FINGERPRINT,
                               'sha256:' + EMPTY_OVERLAY_FINGERPRINT)
