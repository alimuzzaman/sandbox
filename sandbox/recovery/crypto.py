from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

from .errors import RecoveryError
from .integrity import sha256_file


class FixtureCrypto:
    """Deterministic test-only crypto adapter; production wiring must use GnuPG."""
    prefix = b"SBREC1:"

    def encrypt(self, payload: bytes) -> bytes:
        return self.prefix + payload[::-1]

    def decrypt(self, payload: bytes) -> bytes:
        if not payload.startswith(self.prefix): raise RecoveryError("invalid recovery ciphertext", "invalid_ciphertext")
        return payload[len(self.prefix):][::-1]


class GpgCrypto:
    """Symmetric GnuPG adapter using a dedicated inherited passphrase descriptor."""

    def __init__(self, passphrase: str, *, executable: str = "gpg") -> None:
        if not passphrase:
            raise RecoveryError("recovery passphrase is unavailable", "missing_passphrase")
        if any(char in passphrase for char in "\r\n\0"):
            raise RecoveryError("recovery passphrase contains unsafe control text", "invalid_passphrase")
        self._passphrase = passphrase
        self.executable = executable

    def _run(self, argv: list[str], *, input_path: Path, output_path: Path) -> None:
        read_fd, write_fd = os.pipe()
        try:
            pending = self._passphrase.encode() + b"\n"
            while pending:
                written = os.write(write_fd, pending)
                if written <= 0:
                    raise RecoveryError("could not write recovery passphrase descriptor", "passphrase_pipe_failed")
                pending = pending[written:]
            os.close(write_fd); write_fd = -1
            command = [self.executable, "--batch", "--yes", "--pinentry-mode", "loopback",
                       "--passphrase-fd", str(read_fd), *argv, "--output", str(output_path), str(input_path)]
            result = subprocess.run(command, pass_fds=(read_fd,), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    text=True, check=False)
            if result.returncode != 0:
                raise RecoveryError("GnuPG encryption operation failed", "gpg_failed")
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            os.close(read_fd)

    def encrypt_file(self, plaintext: str | Path, ciphertext: str | Path) -> Path:
        source, target = Path(plaintext), Path(ciphertext)
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_name(target.name + ".pending")
        try:
            self._run(["--symmetric", "--cipher-algo", "AES256"], input_path=source, output_path=pending)
            if not pending.exists() or not pending.stat().st_size:
                raise RecoveryError("ciphertext is empty", "gpg_failed")
            pending.replace(target)
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
        return target

    def decrypt_file(self, ciphertext: str | Path, plaintext: str | Path) -> Path:
        source, target = Path(ciphertext), Path(plaintext)
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_name(target.name + ".pending")
        try:
            self._run(["--decrypt"], input_path=source, output_path=pending)
            if not pending.exists():
                raise RecoveryError("decrypted plaintext is absent", "gpg_failed")
            pending.replace(target)
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
        return target

    def verify_file(self, plaintext: str | Path, ciphertext: str | Path) -> str:
        source = Path(plaintext)
        with tempfile.TemporaryDirectory(prefix="sandbox-recovery-verify-") as directory:
            restored = Path(directory) / "restored"
            self.decrypt_file(ciphertext, restored)
            if sha256_file(source) != sha256_file(restored):
                raise RecoveryError("decrypted ciphertext hash does not match", "ciphertext_verification_failed")
        return sha256_file(source)
