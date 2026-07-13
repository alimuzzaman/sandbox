from __future__ import annotations

import hashlib
import json

from .errors import RecoveryError


class CaptureCoordinator:
    def __init__(self, crypto, drive) -> None:
        self.crypto, self.drive = crypto, drive

    def publish(self, set_id: str, artifacts: dict[str, bytes]) -> dict:
        if not artifacts: raise RecoveryError("recovery set has no artifacts", "empty_set")
        payload = b"".join(name.encode() + b"\0" + value for name, value in sorted(artifacts.items()))
        ciphertext = self.crypto.encrypt(payload); cipher_key = f"sets/{set_id}/archive.bin"
        self.drive.put(cipher_key, ciphertext)
        if self.drive.get(cipher_key) != ciphertext or self.crypto.decrypt(ciphertext) != payload:
            raise RecoveryError("ciphertext verification failed", "ciphertext_verification_failed")
        manifest = {"schema_version": 1, "id": set_id, "status": "complete", "artifacts": sorted(artifacts),
                    "ciphertext_object": cipher_key, "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
                    "ciphertext_size": len(ciphertext)}
        self.drive.put(f"sets/{set_id}/manifest.json", json.dumps(manifest, sort_keys=True).encode())
        return manifest

    def verify(self, set_id: str) -> bool:
        manifest = json.loads(self.drive.get(f"sets/{set_id}/manifest.json"))
        cipher = self.drive.get(manifest["ciphertext_object"])
        return (manifest.get("status") == "complete" and hashlib.sha256(cipher).hexdigest() == manifest["ciphertext_sha256"]
                and len(cipher) == manifest["ciphertext_size"])
