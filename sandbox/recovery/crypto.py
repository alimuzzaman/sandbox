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

    def __init__(self, passphrase: str, *, executable: str = "gpg", timeout_seconds: int = 900) -> None:
        if not isinstance(passphrase, str) or not passphrase:
            raise RecoveryError("recovery passphrase is unavailable", "missing_passphrase")
        if len(passphrase) > 4096:
            raise RecoveryError("recovery passphrase is too long", "invalid_passphrase")
        if any(char in passphrase for char in "\r\n\0"):
            raise RecoveryError("recovery passphrase contains unsafe control text", "invalid_passphrase")
        self._passphrase = passphrase
        self.executable = executable
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
            raise RecoveryError("GnuPG timeout is invalid", "invalid_gpg_timeout")
        self.timeout_seconds = timeout_seconds

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
            try:
                result = subprocess.run(command, pass_fds=(read_fd,), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        timeout=self.timeout_seconds, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RecoveryError("GnuPG operation timed out", "gpg_timeout") from exc
            if result.returncode != 0:
                raise RecoveryError("GnuPG encryption operation failed", "gpg_failed")
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            os.close(read_fd)

    @staticmethod
    def _create_pending(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RecoveryError("pending recovery output already exists", "pending_output_exists") from exc
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def encrypt_file(self, plaintext: str | Path, ciphertext: str | Path) -> Path:
        source, target = Path(plaintext), Path(ciphertext)
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_name(target.name + ".pending")
        created = False
        try:
            self._create_pending(pending); created = True
            self._run(["--symmetric", "--cipher-algo", "AES256"], input_path=source, output_path=pending)
            os.chmod(pending, 0o600)
            if not pending.stat().st_size:
                raise RecoveryError("ciphertext is empty", "gpg_failed")
            pending.replace(target)
            os.chmod(target, 0o600)
        except BaseException:
            if created:
                pending.unlink(missing_ok=True)
            raise
        return target

    def decrypt_file(self, ciphertext: str | Path, plaintext: str | Path) -> Path:
        source, target = Path(ciphertext), Path(plaintext)
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_name(target.name + ".pending")
        created = False
        try:
            self._create_pending(pending); created = True
            self._run(["--decrypt"], input_path=source, output_path=pending)
            os.chmod(pending, 0o600)
            if not pending.stat().st_size:
                raise RecoveryError("decrypted plaintext is absent", "gpg_failed")
            pending.replace(target)
            os.chmod(target, 0o600)
        except BaseException:
            if created:
                pending.unlink(missing_ok=True)
            raise
        return target

    def verify_file(self, plaintext: str | Path, ciphertext: str | Path) -> str:
        source = Path(plaintext)
        source_digest = sha256_file(source)
        with tempfile.TemporaryDirectory(prefix="sandbox-recovery-verify-") as directory:
            restored = Path(directory) / "restored"
            self.decrypt_file(ciphertext, restored)
            if source_digest != sha256_file(restored):
                raise RecoveryError("decrypted ciphertext hash does not match", "ciphertext_verification_failed")
        if source_digest != sha256_file(source):
            raise RecoveryError("plaintext changed during ciphertext verification", "source_changed")
        return source_digest
