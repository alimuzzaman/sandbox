"""Deterministic stdlib-only ZIP corpus for exact archive review tests.

The generator returns bytes instead of checking archives into Git.  Every entry
uses a fixed DOS timestamp, explicit Unix mode, and a fixed insertion order, so
the same fixture has the same archive SHA on every supported host.  A small
``main`` entry point is provided for investigators who want to materialise the
corpus locally::

    python tests/fixtures/plugin_check_archive.py /tmp/plugin-check-archives

The invalid cases are intentionally independent.  This keeps a rejection test
diagnostic: a traversal archive does not also depend on a malformed central
directory, and the ZIP-bomb-shaped cases alter only declared metadata rather
than allocating a large file in the repository.
"""

from __future__ import annotations

import argparse
import io
import stat
import struct
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_EPOCH = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class ArchiveFixture:
    name: str
    data: bytes
    expected_error: str | None


def _info(name: str, *, directory: bool = False, mode: int | None = None, compression: int = zipfile.ZIP_STORED) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_EPOCH)
    info.create_system = 3
    info.compress_type = compression
    if directory:
        info.external_attr = (stat.S_IFDIR | 0o755) << 16
    else:
        info.external_attr = (mode if mode is not None else stat.S_IFREG | 0o644) << 16
    return info


def _zip(entries: Iterable[tuple[str, bytes, dict]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
        for name, payload, options in entries:
            archive.writestr(_info(name, **options), payload)
    return output.getvalue()


def _valid_entries(*, main_name: str = "entrypoint.php") -> list[tuple[str, bytes, dict]]:
    return [
        ("demo-plugin/", b"", {"directory": True}),
        (
            f"demo-plugin/{main_name}",
            b"<?php\n/**\n * Plugin Name: Demo Plugin\n * Version: 1.0.0\n */\n",
            {},
        ),
        (
            "demo-plugin/includes/findings.php",
            b"<?php\n// SANDBOX_FIXTURE_ERROR: wp_deprecated_function\n"
            b"// SANDBOX_FIXTURE_WARNING: nonce_check\n",
            {},
        ),
        (
            "demo-plugin/side-effect-sentinel.php",
            b"<?php\nfile_put_contents('/tmp/sandbox-archive-fixture-sentinel', 'executed');\n",
            {},
        ),
    ]


def _patch_flags(data: bytes, bit: int = 0x1) -> bytes:
    patched = bytearray(data)
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        cursor = 0
        while True:
            index = patched.find(signature, cursor)
            if index < 0:
                break
            current = struct.unpack_from("<H", patched, index + offset)[0]
            struct.pack_into("<H", patched, index + offset, current | bit)
            cursor = index + len(signature)
    return bytes(patched)


def _patch_crc(data: bytes) -> bytes:
    patched = bytearray(data)
    changed = False
    for signature, offset, name_offset, name_size_offset in (
        (b"PK\x03\x04", 14, 30, 26),
        (b"PK\x01\x02", 16, 46, 28),
    ):
        cursor = 0
        while True:
            index = patched.find(signature, cursor)
            if index < 0:
                break
            name_size = struct.unpack_from("<H", patched, index + name_size_offset)[0]
            name = bytes(patched[index + name_offset:index + name_offset + name_size])
            if name == b"demo-plugin/entrypoint.php":
                current = struct.unpack_from("<I", patched, index + offset)[0]
                struct.pack_into("<I", patched, index + offset, current ^ 0xFFFFFFFF)
                changed = True
                break
            cursor = index + len(signature)
    if not changed:
        raise AssertionError("fixture has no ZIP headers")
    return bytes(patched)


def _patch_member_sizes(data: bytes, *, expanded: int, compressed: int) -> bytes:
    """Patch the valid fixture's main file local and central sizes only."""

    patched = bytearray(data)
    local = -1
    central = -1
    for signature, name_offset, name_size_offset in (
        (b"PK\x03\x04", 30, 26),
        (b"PK\x01\x02", 46, 28),
    ):
        cursor = 0
        while True:
            index = patched.find(signature, cursor)
            if index < 0:
                break
            name_size = struct.unpack_from("<H", patched, index + name_size_offset)[0]
            name = bytes(patched[index + name_offset:index + name_offset + name_size])
            if name == b"demo-plugin/entrypoint.php":
                if signature == b"PK\x03\x04":
                    local = index
                else:
                    central = index
                break
            cursor = index + len(signature)
    if local < 0 or central < 0:
        raise AssertionError("fixture has no ZIP headers")
    struct.pack_into("<I", patched, local + 18, compressed)
    struct.pack_into("<I", patched, local + 22, expanded)
    struct.pack_into("<I", patched, central + 20, compressed)
    struct.pack_into("<I", patched, central + 24, expanded)
    return bytes(patched)


def _inject_nul_name(data: bytes) -> bytes:
    """Insert a NUL into the main filename in both ZIP headers.

    ``zipfile`` strips NULs while constructing ``ZipInfo`` objects, so this
    malformed case is patched after writing and is caught by the preflight's
    raw-header scan.
    """

    patched = bytearray(data)
    changed = False
    for signature, name_offset, name_size_offset in (
        (b"PK\x03\x04", 30, 26),
        (b"PK\x01\x02", 46, 28),
    ):
        index = patched.find(signature)
        while index >= 0:
            name_size = struct.unpack_from("<H", patched, index + name_size_offset)[0]
            name_start = index + name_offset
            name = bytes(patched[name_start:name_start + name_size])
            if name == b"demo-plugin/entrypoint.php":
                patched[name_start + len(b"demo-plugin/")] = 0
                changed = True
                break
            index = patched.find(signature, index + len(signature))
    if not changed:
        raise AssertionError("fixture has no main filename")
    return bytes(patched)


def build_fixture_corpus() -> dict[str, ArchiveFixture]:
    """Return the complete named fixture corpus."""

    valid = _zip(_valid_entries())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        duplicate = _zip(
            [
                ("demo-plugin/", b"", {"directory": True}),
                ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
                ("demo-plugin/duplicate.php", b"one", {}),
                ("demo-plugin/duplicate.php", b"two", {}),
            ]
        )
    unicode_collision = _zip(
        [
            ("demo-plugin/", b"", {"directory": True}),
            ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
            ("demo-plugin/caf\u00e9.php", b"one", {}),
            ("demo-plugin/cafe\u0301.php", b"two", {}),
        ]
    )
    case_collision = _zip(
        [
            ("demo-plugin/", b"", {"directory": True}),
            ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
            ("demo-plugin/Foo.php", b"one", {}),
            ("demo-plugin/foo.php", b"two", {}),
        ]
    )
    separator_collision = _zip(
        [
            ("demo-plugin/", b"", {"directory": True}),
            ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
            ("demo-plugin/nested/file.php", b"one", {}),
            ("demo-plugin/nested\\file.php", b"two", {}),
        ]
    )
    symlink = _zip(
        [
            ("demo-plugin/", b"", {"directory": True}),
            ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
            ("demo-plugin/link.php", b"entrypoint.php", {"mode": stat.S_IFLNK | 0o777}),
        ]
    )
    fifo = _zip(
        [
            ("demo-plugin/", b"", {"directory": True}),
            ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
            ("demo-plugin/device", b"", {"mode": stat.S_IFIFO | 0o644}),
        ]
    )
    crc_bad = _patch_crc(valid)
    encrypted = _patch_flags(valid)
    nul_name = _inject_nul_name(valid)
    expanded_limit = _patch_member_sizes(valid, expanded=64 * 1024 * 1024 + 1, compressed=1)
    ratio_limit = _zip(
        [
            ("demo-plugin/", b"", {"directory": True}),
            ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
            ("demo-plugin/repetitive.txt", b"A" * 4096, {"compression": zipfile.ZIP_DEFLATED}),
        ]
    )
    path_limit = _zip(
        [
            ("demo-plugin/", b"", {"directory": True}),
            ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
            ("demo-plugin/" + ("x" * 240) + ".txt", b"x", {}),
        ]
    )
    depth_limit = _zip(
        [
            ("demo-plugin/", b"", {"directory": True}),
            ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
            ("demo-plugin/" + "/".join(f"d{i}" for i in range(32)) + "/deep.txt", b"x", {}),
        ]
    )
    member_limit = _zip(
        [("demo-plugin/", b"", {"directory": True})]
        + [(f"demo-plugin/files/{index:05d}.txt", b"x", {}) for index in range(10_000)]
    )

    return {
        "valid": ArchiveFixture("valid", valid, None),
        "valid_non_slug_main": ArchiveFixture(
            "valid_non_slug_main", _zip(_valid_entries(main_name="bootstrap.php")), None
        ),
        "traversal": ArchiveFixture(
            "traversal",
            _zip(
                [
                    ("demo-plugin/", b"", {"directory": True}),
                    ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
                    ("demo-plugin/../escape.php", b"bad", {}),
                ]
            ),
            "archive_member_path",
        ),
        "duplicate": ArchiveFixture("duplicate", duplicate, "archive_member_collision"),
        "unicode_collision": ArchiveFixture("unicode_collision", unicode_collision, "archive_member_collision"),
        "case_collision": ArchiveFixture("case_collision", case_collision, "archive_member_collision"),
        "separator_collision": ArchiveFixture("separator_collision", separator_collision, "archive_member_collision"),
        "drive_name": ArchiveFixture(
            "drive_name", _zip([("C:\\escape.php", b"bad", {})]), "archive_member_path"
        ),
        "unc_name": ArchiveFixture(
            "unc_name", _zip([("\\\\server\\share\\escape.php", b"bad", {})]), "archive_member_path"
        ),
        "nul_name": ArchiveFixture("nul_name", nul_name, "archive_member_path"),
        "symlink": ArchiveFixture("symlink", symlink, "archive_member_special"),
        "fifo": ArchiveFixture("fifo", fifo, "archive_member_special"),
        "encrypted": ArchiveFixture("encrypted", encrypted, "archive_encrypted"),
        "truncated": ArchiveFixture("truncated", valid[:-22], "archive_invalid_zip"),
        "crc": ArchiveFixture("crc", crc_bad, "archive_member_crc"),
        "rootless": ArchiveFixture(
            "rootless",
            _zip([("entrypoint.php", _valid_entries()[1][1], {})]),
            "archive_root_layout",
        ),
        "multi_root": ArchiveFixture(
            "multi_root",
            _zip(
                [
                    ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
                    ("other-plugin/other.php", b"<?php\n", {}),
                ]
            ),
            "archive_root_layout",
        ),
        "file_directory_collision": ArchiveFixture(
            "file_directory_collision",
            _zip(
                [
                    ("demo-plugin/", b"", {"directory": True}),
                    ("demo-plugin/entrypoint.php", _valid_entries()[1][1], {}),
                    ("demo-plugin/node", b"file", {}),
                    ("demo-plugin/node/child.txt", b"child", {}),
                ]
            ),
            "archive_member_collision",
        ),
        "missing_header": ArchiveFixture(
            "missing_header",
            _zip(
                [
                    ("demo-plugin/", b"", {"directory": True}),
                    ("demo-plugin/entrypoint.php", b"<?php\n// no header\n", {}),
                ]
            ),
            "archive_main_file_ambiguous",
        ),
        "ambiguous_header": ArchiveFixture(
            "ambiguous_header",
            _zip(
                [
                    ("demo-plugin/", b"", {"directory": True}),
                    ("demo-plugin/one.php", _valid_entries()[1][1], {}),
                    ("demo-plugin/two.php", _valid_entries()[1][1], {}),
                ]
            ),
            "archive_main_file_ambiguous",
        ),
        "special": ArchiveFixture("special", fifo, "archive_member_special"),
        "expanded_limit": ArchiveFixture("expanded_limit", expanded_limit, "archive_file_limit"),
        "ratio_limit": ArchiveFixture("ratio_limit", ratio_limit, "archive_ratio_limit"),
        "path_limit": ArchiveFixture("path_limit", path_limit, "archive_path_limit"),
        "depth_limit": ArchiveFixture("depth_limit", depth_limit, "archive_path_limit"),
        "member_limit": ArchiveFixture("member_limit", member_limit, "archive_member_limit"),
        "malformed": ArchiveFixture("malformed", b"not a ZIP archive", "archive_invalid_zip"),
    }


def write_fixture_corpus(destination: str | Path) -> dict[str, Path]:
    """Materialise the corpus and return name-to-path mappings."""

    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, fixture in build_fixture_corpus().items():
        path = root / f"{name}.zip"
        path.write_bytes(fixture.data)
        paths[name] = path
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    write_fixture_corpus(args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
