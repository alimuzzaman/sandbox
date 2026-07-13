from __future__ import annotations

from .errors import RecoveryError


class FixtureCrypto:
    """Deterministic test-only crypto adapter; production wiring must use GnuPG."""
    prefix = b"SBREC1:"

    def encrypt(self, payload: bytes) -> bytes:
        return self.prefix + payload[::-1]

    def decrypt(self, payload: bytes) -> bytes:
        if not payload.startswith(self.prefix): raise RecoveryError("invalid recovery ciphertext", "invalid_ciphertext")
        return payload[len(self.prefix):][::-1]
