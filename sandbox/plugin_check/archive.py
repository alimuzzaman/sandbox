"""Pure host-side preflight for the exact Plugin Check archive contract.

This module deliberately has no Sandbox lifecycle or WordPress dependency.  It
does three things only:

* opens one regular ZIP input with ``O_NOFOLLOW``;
* validates and hashes every member without extracting it; and
* optionally extracts the already-validated members through that same open
  descriptor and ``ZipFile`` object.

The archive command will build its disposable runtime around this boundary in a
later task.  Keeping this layer pure makes hostile ZIP cases cheap to test and
prevents a malformed archive from reaching Docker, the registry, or a caller's
checkout.
"""

from __future__ import annotations

import binascii
import hashlib
import json
import os
import re
import stat
import struct
import unicodedata
import zipfile
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Iterable


@dataclass(frozen=True)
class ArchiveLimits:
    """Inclusive limits applied before any archive member is extracted."""

    archive_bytes: int = 128 * 1024 * 1024
    max_members: int = 10_000
    max_path_bytes: int = 240
    max_path_depth: int = 32
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: int = 100
    header_bytes: int = 128 * 1024
    read_chunk_bytes: int = 64 * 1024


DEFAULT_LIMITS = ArchiveLimits()


class ArchivePreflightError(ValueError):
    """A typed, fail-closed archive validation or extraction error."""

    def __init__(self, code: str, message: str, *, member: str | None = None):
        self.code = code
        self.member = member
        detail = f"{message} (member {member!r})" if member else message
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ArchiveMember:
    """Stable, non-secret metadata for one canonical archive member."""

    name: str
    kind: str
    compressed_size: int
    expanded_size: int
    crc32: int
    sha256: str

    def manifest_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def type(self) -> str:
        """Contract spelling for callers that describe a member's type."""

        return self.kind


@dataclass(frozen=True)
class ArchivePreflight:
    """Validated archive identity and canonical member manifest."""

    archive_path: Path
    archive_sha256: str
    archive_slug: str
    main_file: str
    members: tuple[ArchiveMember, ...]
    member_manifest_sha256: str
    total_expanded_bytes: int

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def manifest_sha256(self) -> str:
        """Short alias for the persisted member-manifest digest."""

        return self.member_manifest_sha256


@dataclass(frozen=True)
class _ValidatedRecord:
    info: zipfile.ZipInfo
    member: ArchiveMember
    collision_key: str


# Keep archive identity validation aligned with Sandbox's existing project
# slug resolver: lowercase ASCII letters/numbers, then lowercase letters,
# numbers, underscores, or hyphens.  The canonical member-path limit remains
# the bound on the root name itself.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_PLUGIN_HEADER_RE = re.compile(
    r"^[ \t]*(?:[#/*;]+[ \t]*)?Plugin[ \t]+Name[ \t]*:[ \t]*([^\r\n]+)",
    re.IGNORECASE | re.MULTILINE,
)


def _error(code: str, message: str, *, member: str | None = None) -> ArchivePreflightError:
    return ArchivePreflightError(code, message, member=member)


def _canonical_input_path(path: os.PathLike[str] | str) -> Path:
    try:
        raw = os.fspath(path)
        if isinstance(raw, bytes):
            raw = os.fsdecode(raw)
        return Path(os.path.abspath(raw))
    except (OSError, TypeError, ValueError) as exc:
        raise _error("archive_path_invalid", "archive path is not usable") from exc


def _open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise _error("archive_nofollow_unavailable", "host cannot enforce O_NOFOLLOW")
    flags = os.O_RDONLY | nofollow
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _hash_stream(stream: BinaryIO, expected_size: int, *, limits: ArchiveLimits) -> str:
    digest = hashlib.sha256()
    seen = 0
    stream.seek(0)
    while True:
        chunk = stream.read(limits.read_chunk_bytes)
        if not chunk:
            break
        seen += len(chunk)
        digest.update(chunk)
    if seen != expected_size:
        raise _error(
            "archive_changed",
            f"archive size changed while reading (expected {expected_size}, got {seen})",
        )
    stream.seek(0)
    return digest.hexdigest()


def _mode_kind(info: zipfile.ZipInfo) -> str:
    """Return ``file``/``directory`` while rejecting Unix special entries."""

    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise _error("archive_member_special", "Unix special members are not allowed", member=info.filename)

    trailing_slash = info.filename.replace("\\", "/").endswith("/")
    dos_directory = bool(info.external_attr & 0x10)
    is_directory = file_type == stat.S_IFDIR or info.is_dir() or dos_directory
    if trailing_slash and not is_directory:
        raise _error("archive_member_type", "a trailing slash must identify a directory", member=info.filename)
    if is_directory and not trailing_slash and file_type != stat.S_IFDIR:
        raise _error("archive_member_type", "directory entries must end with '/'", member=info.filename)
    return "directory" if is_directory else "file"


def _canonical_member_name(info: zipfile.ZipInfo, limits: ArchiveLimits) -> tuple[str, str, str]:
    raw = info.filename
    if "\x00" in raw:
        raise _error("archive_member_path", "NUL is not allowed in a member name", member=raw)
    kind = _mode_kind(info)
    name = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    if kind == "directory":
        name = name.rstrip("/")
    if not name or name.startswith("/") or name.startswith("//"):
        raise _error("archive_member_path", "absolute and UNC member names are not allowed", member=raw)
    if _DRIVE_RE.match(name):
        raise _error("archive_member_path", "drive-letter member names are not allowed", member=raw)
    parts = name.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise _error("archive_member_path", "empty, '.' and '..' components are not allowed", member=raw)
    try:
        path_bytes = len(name.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise _error("archive_member_path", "member name is not valid UTF-8", member=raw) from exc
    if path_bytes > limits.max_path_bytes:
        raise _error(
            "archive_path_limit",
            f"canonical path is {path_bytes} UTF-8 bytes (limit {limits.max_path_bytes})",
            member=raw,
        )
    if len(parts) > limits.max_path_depth:
        raise _error(
            "archive_path_limit",
            f"canonical path depth is {len(parts)} (limit {limits.max_path_depth})",
            member=raw,
        )
    return name, name.casefold(), kind


def _validate_slug(root: str) -> str:
    if not _SLUG_RE.fullmatch(root):
        raise _error("archive_slug_invalid", f"top-level directory {root!r} is not a WordPress slug")
    return root


def _header_in(sample: bytes) -> bool:
    text = sample.decode("utf-8-sig", errors="replace")
    match = _PLUGIN_HEADER_RE.search(text)
    return bool(match and match.group(1).strip())


def _manifest_digest(members: Iterable[ArchiveMember]) -> str:
    payload = json.dumps(
        [member.manifest_dict() for member in sorted(members, key=lambda item: item.name)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ArchiveSession(AbstractContextManager["ArchiveSession"]):
    """One open archive descriptor shared by inspection and extraction."""

    def __init__(self, path: os.PathLike[str] | str, *, limits: ArchiveLimits = DEFAULT_LIMITS):
        self.path = _canonical_input_path(path)
        self.limits = limits
        self._fd: int | None = None
        self._stream: BinaryIO | None = None
        self._zip: zipfile.ZipFile | None = None
        self._records: tuple[_ValidatedRecord, ...] | None = None
        self._result: ArchivePreflight | None = None

    def __enter__(self) -> "ArchiveSession":
        if self._stream is not None:
            raise RuntimeError("archive session cannot be entered twice")
        try:
            self._fd = os.open(self.path, _open_flags())
        except FileNotFoundError as exc:
            raise _error("archive_not_found", f"archive does not exist: {self.path}") from exc
        except (OSError, ValueError, TypeError) as exc:
            if getattr(exc, "errno", None) in {40, getattr(os, "ELOOP", 62)}:
                raise _error("archive_symlink", "archive input must not be a symlink") from exc
            if isinstance(exc, ValueError):
                raise _error("archive_path_invalid", "archive path is not usable") from exc
            raise _error("archive_open_failed", f"cannot open archive: {self.path}") from exc

        try:
            descriptor_stat = os.fstat(self._fd)
            if not stat.S_ISREG(descriptor_stat.st_mode):
                raise _error("archive_not_regular", "archive input must be a regular file")
            if descriptor_stat.st_size > self.limits.archive_bytes:
                raise _error(
                    "archive_size_limit",
                    f"archive is {descriptor_stat.st_size} bytes (limit {self.limits.archive_bytes})",
                )
            if descriptor_stat.st_size <= 0:
                raise _error("archive_invalid_zip", "archive is empty")
            self._stream = os.fdopen(self._fd, "rb")
            self._fd = self._stream.fileno()
            archive_sha256 = _hash_stream(self._stream, descriptor_stat.st_size, limits=self.limits)
            after_stat = os.fstat(self._stream.fileno())
            if (
                after_stat.st_dev != descriptor_stat.st_dev
                or after_stat.st_ino != descriptor_stat.st_ino
                or after_stat.st_size != descriptor_stat.st_size
            ):
                raise _error("archive_changed", "archive identity changed while it was read")
            try:
                self._zip = zipfile.ZipFile(self._stream, "r", allowZip64=True)
            except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                raise _error("archive_invalid_zip", "archive central directory is invalid") from exc
            self._archive_sha256 = archive_sha256
            return self
        except Exception:
            self.close()
            raise

    @property
    def descriptor_fileno(self) -> int:
        """The descriptor used by both ``inspect`` and ``extract_to``."""

        if self._stream is None:
            raise RuntimeError("archive session is not open")
        return self._stream.fileno()

    @property
    def archive_sha256(self) -> str:
        if self._stream is None:
            raise RuntimeError("archive session is not open")
        return self._archive_sha256

    def _ensure_open(self) -> tuple[BinaryIO, zipfile.ZipFile]:
        if self._stream is None or self._zip is None:
            raise RuntimeError("archive session is not open")
        return self._stream, self._zip

    def _read_member(self, info: zipfile.ZipInfo) -> str:
        """Hash one member through this session's already-open ``ZipFile``."""

        _, archive = self._ensure_open()
        digest = hashlib.sha256()
        crc = 0
        seen = 0
        try:
            with archive.open(info, "r") as source:
                while True:
                    chunk = source.read(self.limits.read_chunk_bytes)
                    if not chunk:
                        break
                    seen += len(chunk)
                    if seen > self.limits.max_file_bytes:
                        raise _error(
                            "archive_file_limit",
                            f"expanded bytes exceed {self.limits.max_file_bytes}",
                            member=info.filename,
                        )
                    digest.update(chunk)
                    crc = binascii.crc32(chunk, crc) & 0xFFFFFFFF
        except ArchivePreflightError:
            raise
        except zipfile.BadZipFile as exc:
            code = "archive_member_crc" if "CRC" in str(exc).upper() else "archive_member_corrupt"
            raise _error(code, "member could not be read or verified", member=info.filename) from exc
        except (EOFError, OSError, RuntimeError, ValueError, zipfile.LargeZipFile) as exc:
            raise _error("archive_member_corrupt", "member could not be read or verified", member=info.filename) from exc
        if seen != info.file_size:
            raise _error(
                "archive_member_size_mismatch",
                f"expanded size is {seen}, declared {info.file_size}",
                member=info.filename,
            )
        if crc != info.CRC:
            raise _error("archive_member_crc", "CRC-32 does not match the declared value", member=info.filename)
        return digest.hexdigest()

    def _reject_raw_nul_names(self, infos: list[zipfile.ZipInfo]) -> None:
        """Catch NULs that ``zipfile`` silently truncates from ``ZipInfo.filename``."""

        stream, archive = self._ensure_open()
        try:
            # Inspect the central-directory names as raw bytes.  This is needed
            # because ZipInfo normalises a name at the first NUL before callers
            # can validate it.  Keep the scan on the same descriptor used by
            # hashing and member reads.
            stream.seek(archive.start_dir)
            for _ in infos:
                header = stream.read(46)
                if len(header) != 46 or header[:4] != b"PK\x01\x02":
                    raise _error("archive_invalid_zip", "archive central directory is truncated")
                name_size, extra_size, comment_size = struct.unpack_from("<HHH", header, 28)
                raw_name = stream.read(name_size)
                if len(raw_name) != name_size:
                    raise _error("archive_invalid_zip", "archive central member name is truncated")
                if b"\x00" in raw_name:
                    raise _error("archive_member_path", "NUL is not allowed in a member name")
                if len(stream.read(extra_size + comment_size)) != extra_size + comment_size:
                    raise _error("archive_invalid_zip", "archive central member metadata is truncated")

            # Local headers are checked as well: a malformed archive can make
            # central and local names disagree, and extraction uses the local
            # header's name length.
            for info in infos:
                stream.seek(info.header_offset)
                local = stream.read(30)
                if len(local) != 30 or local[:4] != b"PK\x03\x04":
                    raise _error("archive_invalid_zip", "archive local header is invalid", member=info.filename)
                name_size, extra_size = struct.unpack_from("<HH", local, 26)
                raw_name = stream.read(name_size)
                if len(raw_name) != name_size:
                    raise _error("archive_invalid_zip", "archive local member name is truncated", member=info.filename)
                if b"\x00" in raw_name:
                    raise _error("archive_member_path", "NUL is not allowed in a member name", member=info.filename)
                if len(stream.read(extra_size)) != extra_size:
                    raise _error("archive_invalid_zip", "archive local metadata is truncated", member=info.filename)
        finally:
            stream.seek(0)

    def inspect(self) -> ArchivePreflight:
        """Validate all members and return a stable manifest without extraction."""

        if self._result is not None:
            return self._result
        _, archive = self._ensure_open()
        try:
            infos = archive.infolist()
        except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise _error("archive_invalid_zip", "archive member table is invalid") from exc
        if len(infos) > self.limits.max_members:
            raise _error(
                "archive_member_limit",
                f"archive has {len(infos)} members (limit {self.limits.max_members})",
            )
        self._reject_raw_nul_names(infos)

        records: list[_ValidatedRecord] = []
        by_collision: dict[str, _ValidatedRecord] = {}
        total_expanded = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise _error("archive_encrypted", "encrypted ZIP members are not allowed", member=info.filename)
            if info.file_size < 0 or info.compress_size < 0:
                raise _error("archive_member_size", "negative member size is invalid", member=info.filename)
            name, collision_key, kind = _canonical_member_name(info, self.limits)
            if collision_key in by_collision:
                raise _error("archive_member_collision", "canonical member name collides with another member", member=info.filename)
            if kind == "directory" and info.file_size != 0:
                raise _error("archive_directory_nonempty", "directory entries must have zero expanded bytes", member=info.filename)
            if kind == "file":
                if info.file_size > self.limits.max_file_bytes:
                    raise _error(
                        "archive_file_limit",
                        f"expanded bytes are {info.file_size} (limit {self.limits.max_file_bytes})",
                        member=info.filename,
                    )
                if info.file_size and (info.compress_size == 0 or info.file_size > info.compress_size * self.limits.max_compression_ratio):
                    raise _error(
                        "archive_ratio_limit",
                        f"compression ratio exceeds {self.limits.max_compression_ratio}:1",
                        member=info.filename,
                    )
                total_expanded += info.file_size
                if total_expanded > self.limits.max_total_bytes:
                    raise _error(
                        "archive_total_limit",
                        f"expanded bytes exceed {self.limits.max_total_bytes}",
                        member=info.filename,
                    )
                sha256 = self._read_member(info)
            else:
                sha256 = hashlib.sha256(b"").hexdigest()
            member = ArchiveMember(
                name=name,
                kind=kind,
                compressed_size=info.compress_size,
                expanded_size=info.file_size,
                crc32=info.CRC,
                sha256=sha256,
            )
            record = _ValidatedRecord(info=info, member=member, collision_key=collision_key)
            records.append(record)
            by_collision[collision_key] = record

        kind_by_key = {record.collision_key: record.member.kind for record in records}
        for record in records:
            parts = record.member.name.split("/")
            for index in range(1, len(parts)):
                parent_key = "/".join(parts[:index]).casefold()
                if kind_by_key.get(parent_key) == "file":
                    raise _error(
                        "archive_member_collision",
                        "a file cannot also be an ancestor directory",
                        member=record.member.name,
                    )

        roots = {record.member.name.split("/", 1)[0] for record in records}
        if len(roots) != 1 or any("/" not in record.member.name and record.member.kind != "directory" for record in records):
            raise _error("archive_root_layout", "archive must contain exactly one top-level plugin directory")
        archive_slug = _validate_slug(next(iter(roots)))

        # Header candidates are read again only through the same open descriptor;
        # their names are root-level PHP files by construction.  The second pass
        # avoids retaining archive-controlled source bytes in the result object.
        candidates: list[str] = []
        for record in records:
            name = record.member.name
            if record.member.kind != "file" or len(name.split("/")) != 2 or not name.lower().endswith(".php"):
                continue
            _, archive = self._ensure_open()
            try:
                with archive.open(record.info, "r") as source:
                    sample = source.read(self.limits.header_bytes)
            except (EOFError, OSError, RuntimeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                raise _error("archive_member_corrupt", "main-file candidate could not be read", member=name) from exc
            if _header_in(sample):
                candidates.append(name)
        if len(candidates) != 1:
            raise _error(
                "archive_main_file_ambiguous",
                f"expected exactly one root-level PHP Plugin Name header, found {len(candidates)}",
            )

        members = tuple(sorted((record.member for record in records), key=lambda item: item.name))
        self._records = tuple(sorted(records, key=lambda item: item.member.name))
        self._result = ArchivePreflight(
            archive_path=self.path,
            archive_sha256=self.archive_sha256,
            archive_slug=archive_slug,
            main_file=candidates[0],
            members=members,
            member_manifest_sha256=_manifest_digest(members),
            total_expanded_bytes=total_expanded,
        )
        return self._result

    def _ensure_directory(self, path: Path) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            try:
                path.mkdir(mode=0o755)
            except FileExistsError:
                info = path.lstat()
            else:
                return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _error("archive_extraction_failed", "extraction path is not a directory", member=str(path))
        os.chmod(path, 0o755)

    def extract_to(self, destination: os.PathLike[str] | str, result: ArchivePreflight | None = None) -> Path:
        """Extract validated members safely through this session's descriptor."""

        _, archive = self._ensure_open()
        inspected = self.inspect()
        if result is not None and result != inspected:
            raise _error("archive_extraction_failed", "extraction result is not from this archive session")
        result = inspected
        requested_root = Path(destination)
        try:
            # A platform may expose ordinary temporary directories through a
            # trusted system symlink (macOS commonly maps ``/var`` to
            # ``/private/var``).  Reject a symlink at the requested extraction
            # directory itself, then resolve only the already-existing parent
            # chain before applying the no-symlink check.
            try:
                requested_info = requested_root.lstat()
            except FileNotFoundError:
                requested_info = None
            if requested_info is not None and stat.S_ISLNK(requested_info.st_mode):
                raise _error("archive_extraction_failed", "extraction root must not be a symlink")
            root = Path(os.path.realpath(requested_root))
            self._ensure_no_symlink_chain(root)
            if root.exists() or root.is_symlink():
                if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
                    raise _error("archive_extraction_failed", "extraction root must be an empty directory")
                os.chmod(root, 0o700)
            else:
                root.mkdir(mode=0o700)
            for record in self._records or ():
                relative = Path(*record.member.name.split("/"))
                target = root / relative
                if record.member.kind == "directory":
                    self._ensure_directory(target)
                    continue
                self._ensure_directory(target.parent)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                fd = os.open(target, flags, 0o644)
                digest = hashlib.sha256()
                crc = 0
                seen = 0
                try:
                    with os.fdopen(fd, "wb") as output, archive.open(record.info, "r") as source:
                        while True:
                            chunk = source.read(self.limits.read_chunk_bytes)
                            if not chunk:
                                break
                            seen += len(chunk)
                            digest.update(chunk)
                            crc = binascii.crc32(chunk, crc) & 0xFFFFFFFF
                            if seen > self.limits.max_file_bytes:
                                raise _error("archive_extraction_failed", "expanded file exceeds its limit", member=record.member.name)
                            output.write(chunk)
                    if seen != record.member.expanded_size or crc != record.member.crc32 or digest.hexdigest() != record.member.sha256:
                        raise _error("archive_extraction_failed", "extracted bytes differ from the validated manifest", member=record.member.name)
                except ArchivePreflightError:
                    raise
                except (EOFError, OSError, RuntimeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                    raise _error("archive_extraction_failed", "member extraction failed", member=record.member.name) from exc
                os.chmod(target, 0o644)
            return root
        except ArchivePreflightError:
            raise
        except (OSError, ValueError) as exc:
            raise _error("archive_extraction_failed", "archive could not be extracted") from exc

    @staticmethod
    def _ensure_no_symlink_chain(path: Path) -> None:
        """Reject a destination whose existing parent chain contains a symlink."""

        current = path
        while True:
            try:
                info = current.lstat()
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(info.st_mode):
                    raise _error("archive_extraction_failed", "extraction path contains a symlink", member=str(current))
                if current == current.parent:
                    break
            if current == current.parent:
                break
            current = current.parent

    def close(self) -> None:
        archive, stream = self._zip, self._stream
        descriptor = self._fd if stream is None else None
        self._zip = None
        self._stream = None
        self._fd = None
        if archive is not None:
            try:
                archive.close()
            except OSError:
                pass
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        elif descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


def open_archive(path: os.PathLike[str] | str, *, limits: ArchiveLimits = DEFAULT_LIMITS) -> ArchiveSession:
    """Return a context manager that owns one open archive descriptor."""

    return ArchiveSession(path, limits=limits)


def preflight_archive(
    path: os.PathLike[str] | str,
    *,
    extraction_root: os.PathLike[str] | str | None = None,
    limits: ArchiveLimits = DEFAULT_LIMITS,
) -> ArchivePreflight:
    """Validate an archive and optionally extract it before closing the descriptor."""

    with open_archive(path, limits=limits) as session:
        result = session.inspect()
        if extraction_root is not None:
            session.extract_to(extraction_root, result)
        return result


__all__ = [
    "ArchiveLimits",
    "ArchiveMember",
    "ArchivePreflight",
    "ArchivePreflightError",
    "ArchiveSession",
    "DEFAULT_LIMITS",
    "open_archive",
    "preflight_archive",
]
