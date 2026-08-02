"""Build minimal payload context and stage credentials without transport leakage."""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import fcntl


ENV_ALLOWLIST = frozenset({"PATH", "LANG", "LC_ALL", "TZ", "HOME", "USER", "LOGNAME",
                           "WP_ENVIRONMENT_TYPE", "XDEBUG_TRIGGER"})
DEFAULT_INJECTED_ROOT = Path("/var/lib/sandbox/native/injected")
_MACHINE_ID = re.compile(r"^sb-[a-f0-9]{12,32}$")
_POLICY_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_REFERENCE_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _validate_machine_id(value):
    if not isinstance(value, str) or not _MACHINE_ID.fullmatch(value):
        raise ValueError("credential machine id is invalid")
    return value


def _validate_policy_digest(value):
    if not isinstance(value, str) or not _POLICY_DIGEST.fullmatch(value):
        raise ValueError("credential policy digest is invalid")
    return value


def _reference_name(reference):
    if not isinstance(reference, str) or not reference or "=" in reference:
        raise ValueError("credential reference is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in reference):
        raise ValueError("credential reference is invalid")
    segments = reference.split("/")
    if any(not _REFERENCE_SEGMENT.fullmatch(segment) for segment in segments):
        raise ValueError("credential reference is invalid")
    return segments[-1]


def _validated_references(references):
    result = []
    names = set()
    for reference in tuple(references):
        name = _reference_name(reference)
        if name in names:
            raise ValueError("credential names must be unique")
        names.add(name)
        result.append((reference, name))
    return tuple(result)


class OwnerOnlyCredentialStager:
    """Write opaque credential bytes under one fixed root without following links."""

    def __init__(self, root=DEFAULT_INJECTED_ROOT):
        self.root = Path(root)
        if not self.root.is_absolute():
            raise ValueError("credential injected root must be absolute")

    @staticmethod
    def _directory_fd(parent_fd, name):
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid() \
                or details.st_mode & 0o077:
            os.close(descriptor)
            raise ValueError("credential staging directory is not owner-only")
        return descriptor

    def _root_fd(self):
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(self.root, flags)
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid() \
                or details.st_mode & 0o077:
            os.close(descriptor)
            raise ValueError("credential injected root is not owner-only")
        return descriptor

    def write(self, machine_id, name, value):
        _validate_machine_id(machine_id)
        if not _REFERENCE_SEGMENT.fullmatch(name):
            raise ValueError("credential name is invalid")
        if not isinstance(value, bytes) or not value:
            raise ValueError("credential provider returned invalid bytes")
        root_fd = self._root_fd()
        machine_fd = None
        descriptor = None
        try:
            machine_fd = self._directory_fd(root_fd, machine_id)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
            descriptor = os.open(name, flags, 0o600, dir_fd=machine_fd)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
            return self.root / machine_id / name
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            try:
                if machine_fd is not None:
                    os.unlink(name, dir_fd=machine_fd)
            except FileNotFoundError:
                pass
            raise
        finally:
            if machine_fd is not None:
                os.close(machine_fd)
            os.close(root_fd)

    def cleanup(self, machine_id, name, path):
        _validate_machine_id(machine_id)
        expected = self.root / machine_id / name
        if Path(path) != expected:
            raise ValueError("credential staging path is invalid")
        root_fd = self._root_fd()
        machine_fd = None
        try:
            machine_fd = self._directory_fd(root_fd, machine_id)
            os.unlink(name, dir_fd=machine_fd)
        except FileNotFoundError:
            pass
        finally:
            if machine_fd is not None:
                os.close(machine_fd)
            os.close(root_fd)


class HelperCredentialInstaller:
    """Install one staged credential through the fixed privileged helper verb.

    The staging pathname is validated locally but intentionally omitted from the
    subprocess argv: the helper independently resolves the same fixed path.
    """

    def __init__(self, *, process, helper, injected_root=DEFAULT_INJECTED_ROOT):
        try:
            helper_path = os.fspath(helper)
        except TypeError as exc:
            raise ValueError("credential helper path is invalid") from exc
        if (not isinstance(helper_path, str) or not Path(helper_path).is_absolute()
                or ".." in Path(helper_path).parts
                or any(ord(character) < 32 or ord(character) == 127
                       for character in helper_path)):
            raise ValueError("credential helper path is invalid")
        root = Path(injected_root)
        if not root.is_absolute():
            raise ValueError("credential injected root must be absolute")
        self.process = process
        self.helper = helper_path
        self.root = root

    def install(self, machine_id, policy_digest, name, path):
        machine_id = _validate_machine_id(machine_id)
        policy_digest = _validate_policy_digest(policy_digest)
        if not isinstance(name, str) or not _REFERENCE_SEGMENT.fullmatch(name):
            raise ValueError("credential name is invalid")
        expected = self.root / machine_id / name
        try:
            staged_path = os.fspath(path)
        except TypeError as exc:
            raise ValueError("credential staging path is invalid") from exc
        if not isinstance(staged_path, str) or staged_path != str(expected):
            raise ValueError("credential staging path is invalid")
        result = self.process.run(
            ("sudo", "-n", self.helper, "credential-install", machine_id,
             policy_digest, name),
            timeout=120,
        )
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError("managed credential installation failed")

    def __call__(self, machine_id, policy_digest, name, path):
        self.install(machine_id, policy_digest, name, path)


class CredentialInjector:
    """Stage bytes out-of-band; installer receives names and paths, never secret values."""

    def __init__(self, *, secret_provider, installer, staging_writer=None,
                 staging_cleanup=None, injected_root=DEFAULT_INJECTED_ROOT):
        self.secret_provider = secret_provider
        self.installer = installer
        self._stager = None
        if staging_writer is None:
            self._stager = OwnerOnlyCredentialStager(injected_root)
            self.staging_writer = self._stager.write
            self.staging_cleanup = self._default_cleanup
        else:
            self.staging_writer = staging_writer
            self.staging_cleanup = staging_cleanup or (lambda _machine, _name, path: Path(path).unlink(missing_ok=True))

    def _default_cleanup(self, machine_id, name, path):
        self._stager.cleanup(machine_id, name, path)

    def install(self, *, machine_id, policy_digest, references):
        machine_id = _validate_machine_id(machine_id)
        policy_digest = _validate_policy_digest(policy_digest)
        reference_names = _validated_references(references)
        installed = []
        for reference, name in reference_names:
            secret = self.secret_provider(reference)
            if not isinstance(secret, (bytes, bytearray)) or not secret:
                raise ValueError("credential provider returned invalid bytes")
            value = bytes(secret)
            if isinstance(secret, bytearray):
                secret[:] = b"\x00" * len(secret)
            path = self.staging_writer(machine_id, name, value)
            try:
                self.installer(machine_id, policy_digest, name, path)
            finally:
                self.staging_cleanup(machine_id, name, path)
            installed.append({"reference": reference, "name": name,
                              "container_path": f"/run/credentials/sandbox/{name}"})
        return tuple(installed)


def sanitize_execution_context(environment, credential_refs=()):
    clean = {key: str(value) for key, value in dict(environment).items()
             if key in ENV_ALLOWLIST and "\x00" not in str(value)}
    refs = tuple(reference for reference, _name in _validated_references(credential_refs))
    return {"environment": clean, "credential_refs": refs,
            "close_fds_from": 3, "pass_fds": (), "control_sockets": ()}


class NativeCredentialStore:
    """Locked machine-local credential bytes outside project and registry state."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.lock_path = self.root / ".lock"

    def _path(self, reference):
        segments = reference.split("/") if isinstance(reference, str) else ()
        if len(segments) < 3 or any(not _REFERENCE_SEGMENT.fullmatch(item) for item in segments):
            raise ValueError("credential reference is invalid")
        # The digest is an opaque filename; a reference can never select a path.
        import hashlib
        return self.root / hashlib.sha256(reference.encode()).hexdigest()

    def __call__(self, reference):
        path = self._path(reference)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        lock = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.exists() or path.is_symlink():
                descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
                try:
                    details = os.fstat(descriptor)
                    if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid()
                            or details.st_mode & 0o077 or not 16 <= details.st_size <= 256):
                        raise ValueError("credential store entry is invalid")
                    value = os.read(descriptor, 257)
                finally:
                    os.close(descriptor)
                if not 16 <= len(value) <= 256:
                    raise ValueError("credential store entry is invalid")
                return value
            value = secrets.token_urlsafe(32).encode()
            descriptor, temporary = tempfile.mkstemp(prefix="credential-", dir=self.root)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(value); output.flush(); os.fsync(output.fileno())
                os.replace(temporary, path)
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try: os.fsync(directory)
                finally: os.close(directory)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
            return value
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
