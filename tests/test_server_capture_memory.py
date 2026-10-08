"""SC-007 memory bounds for 2 GiB sparse server capture and promote paths.

The worker processes use sparse files and bounded fakes. They do not start
Docker or materialize multi-gigabyte payloads on disk.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import unittest
from unittest import mock

from tests.server_capture_support import fake_facts
from tests.subprocess_support import run_test_process

ROOT = Path(__file__).resolve().parents[1]
SPARSE_BYTES = 2 * 1024 * 1024 * 1024
RSS_LIMIT = 256 * 1024 * 1024
ZERO_BLOCK = bytes(1024 * 1024)


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _zero_digest(size: int) -> str:
    digest = hashlib.sha256()
    while size:
        block = min(size, len(ZERO_BLOCK))
        digest.update(ZERO_BLOCK[:block])
        size -= block
    return digest.hexdigest()


def _sparse_file(path: str | Path, size: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.ftruncate(descriptor, size)
    finally:
        os.close(descriptor)


class _SparseTarWriter:
    """Model tar's bounded file streaming while keeping the output sparse."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.member_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        size = 10 * 1024
        for member_size in self.member_sizes:
            size += 512 + ((member_size + 511) // 512) * 512
        _sparse_file(self.path, size)

    def add(self, path, arcname=None, recursive=False):
        del arcname, recursive
        self.member_sizes.append(Path(path).stat().st_size)


def _run_helper_worker(base: Path) -> None:
    from sandbox.recovery import server_capture_helper as helper
    from tests.server_capture_support import request_for

    root = base / "captures"
    slot = "capture-" + "a" * 64
    directory = root / slot
    directory.mkdir(parents=True, mode=0o700)
    request = request_for("fixture-remote", "set-a")
    request["slot"] = slot
    wordpress_size = SPARSE_BYTES - (1024 + 512 + 10 * 1024)
    hashes: dict[str, tuple[str, int]] = {}
    original_hash_file = helper.hash_file

    def capture_hash(path):
        value = original_hash_file(path)
        hashes[str(path)] = value
        return value

    def bounded_query(argv, _env, _deadline, _step, _code):
        text = " ".join(argv)
        if "DATA_LENGTH" in text:
            return b"1000\n"
        if "TABLE_TYPE" in text:
            return b"posts\tBASE TABLE\t5\n"
        return b"1000\t/var/www/html\n"

    def bounded_run_to_file(_argv, path, _env, _deadline, _step, _code):
        target = Path(path)
        if target.name == "database.sql":
            target.write_bytes(b"CREATE TABLE `posts` (id int);\n")
        else:
            _sparse_file(target, wordpress_size)

    def bounded_hash_stream(_argv, _env, _deadline, _step, _code):
        return hashes[str(directory / "work" / "wordpress.tar")]

    request["accepted_at"] = 1.0
    helper.write_private(str(directory), "request.json", helper.canonical(request))
    helper.write_private(str(directory), "state.json", helper.canonical({
        "state": "queued", "phase": None, "accepted_at": 1.0,
        "started_at": None, "ended_at": None, "reason": None, "detail": None}))
    with (mock.patch.object(helper, "query", side_effect=bounded_query),
          mock.patch.object(helper, "run_to_file", side_effect=bounded_run_to_file),
          mock.patch.object(helper, "hash_stream", side_effect=bounded_hash_stream),
          mock.patch.object(helper, "hash_file", side_effect=capture_hash),
          mock.patch.object(helper.tarfile, "open", side_effect=lambda path, _mode:
                            _SparseTarWriter(path))):
        helper.run_job(root, slot, request, b"synthetic-password", 1.0)
    final = helper.load_json(str(directory / "state.json"), 4096)
    if not final or final.get("state") != "complete":
        raise AssertionError("sparse helper job did not complete: " + json.dumps(final))
    if (directory / "archive.tar").stat().st_size != SPARSE_BYTES:
        raise AssertionError("helper did not create the 2 GiB sparse archive")


class _SparseWriteStream:
    def __init__(self, stream) -> None:
        self.stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.stream.close()

    def write(self, payload: bytes) -> int:
        if payload.strip(b"\0"):
            raise AssertionError("memory fixture expected a zero-filled archive")
        size = self.stream.tell() + len(payload)
        self.stream.seek(size)
        self.stream.truncate(size)
        return len(payload)

    def __getattr__(self, name):
        return getattr(self.stream, name)


class _ZeroTransport:
    def __init__(self, facts: dict, digest: str) -> None:
        self.facts = facts
        self.digest = digest
        self.calls: list[str] = []

    def status(self, _remote, _slot):
        self.calls.append("status")
        return self.facts

    def read_receipt(self, _remote, _slot):
        self.calls.append("read_receipt")
        receipt = dict(self.facts["receipt"], schema_version=1,
                       request_id=self.facts["request"]["request_id"],
                       backup_operation_id="set-a",
                       source_binding=self.facts["request"]["source_binding"],
                       inventory={"summary": self.facts["receipt"]["inventory_summary"],
                                  "tables": []})
        return receipt

    def read_declaration(self, _remote, _slot):
        self.calls.append("read_declaration")
        from tests.server_capture_support import declarations_bytes
        return declarations_bytes("set-a").decode()

    def read_chunk(self, _remote, _slot, _offset, length):
        self.calls.append("read_chunk")
        return bytes(length)

    def mark_promoted(self, _remote, _slot, _marker):
        self.calls.append("mark_promoted")
        return {"ok": True, "existing": False}


class _SparseCrypto:
    def encrypt_file(self, source, target):
        _sparse_file(target, Path(source).stat().st_size)
        return Path(target)

    def verify_file(self, source, _target):
        from sandbox.recovery.integrity import sha256_file
        return sha256_file(source)


class _SparseDrive:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.files: dict[str, int] = {}

    def list(self, _prefix=""):
        return [{"Path": key, "Size": len(value)} for key, value in self.objects.items()]

    def put_file(self, key, source):
        self.files[key] = Path(source).stat().st_size

    def verify_file(self, key, source):
        if self.files.get(key) != Path(source).stat().st_size:
            raise AssertionError("sparse publication size changed")

    def put(self, key, payload):
        self.objects[key] = bytes(payload)


def _run_promote_worker(base: Path) -> None:
    from sandbox.recovery import capture as capture_module
    from sandbox.recovery import server_capture as server_module
    from sandbox.recovery.capture import StagingCaptureCoordinator
    from sandbox.recovery.catalog import load_catalog
    size = SPARSE_BYTES
    digest = _zero_digest(size)
    facts = fake_facts("set-a", archive=b"x")
    facts["receipt"]["archive_sha256"] = digest
    facts["receipt"]["archive_size"] = size
    facts["archive_size"] = size
    transport = _ZeroTransport(facts, digest)
    drive = _SparseDrive()
    materialized = base / "materialized"
    materialized.mkdir(mode=0o700)
    capture = StagingCaptureCoordinator(
        _SparseCrypto(), drive, staging_root=base / "staging",
        pending_root=base / "pending", materialization_root=materialized,
        clock=lambda: "2026-10-08T00:00:00Z")
    catalog = load_catalog(ROOT / "config" / "recovery-profiles.json")
    service = server_module.ServerCaptureService(
        catalog, transport, environment={"RECOVERY_PASSPHRASE": "synthetic-passphrase"},
        config={}, clock=lambda: 2_000.0, state_root=base / "operator",
        drive=drive, capture=capture)

    module_open = server_module.open if hasattr(server_module, "open") else open
    part = base / "operator" / "promote" / facts["slot"] / "archive.part"

    def sparse_open(path, mode="r", *args, **kwargs):
        stream = module_open(path, mode, *args, **kwargs)
        return _SparseWriteStream(stream) if Path(path) == part and mode == "r+b" else stream

    with (mock.patch.object(server_module, "CHUNK_BYTES", 8 * 1024 * 1024),
          mock.patch.object(server_module, "open", sparse_open, create=True),
          mock.patch.object(server_module.shutil, "disk_usage",
                            return_value=type("Usage", (), {"free": 4 * size})()),
          mock.patch.object(capture_module.tarfile, "open", side_effect=lambda path, _mode:
                            _SparseTarWriter(path))):
        outcome = service.promote("fixture-remote", "set-a", confirm=True)
    if not outcome.get("ok") or outcome.get("status") != "published":
        raise AssertionError("sparse promote did not publish: " + json.dumps(outcome))
    if drive.files.get("sets/set-a/archive.tar.gpg") != SPARSE_BYTES + 1024 + 512 + 10 * 1024:
        raise AssertionError("promote publication was not a 2 GiB sparse archive")
    if "sets/set-a/manifest.json" not in drive.objects:
        raise AssertionError("promote did not publish the manifest")


def _worker() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"helper", "promote"}:
        return 2
    base = Path(sys.argv[2])
    if sys.argv[1] == "helper":
        _run_helper_worker(base)
    else:
        _run_promote_worker(base)
    print(json.dumps({"peak_rss_bytes": _rss_bytes(), "operation": sys.argv[1]}))
    return 0


class ServerCaptureMemoryTests(unittest.TestCase):
    def _assert_bounded_worker(self, operation: str) -> None:
        with tempfile.TemporaryDirectory(prefix="sandbox-capture-memory-") as temporary:
            result = run_test_process(
                [sys.executable, "-m", "tests.test_server_capture_memory", operation, temporary],
                cwd=ROOT, capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        measurement = json.loads(result.stdout)
        self.assertEqual(measurement["operation"], operation)
        self.assertLess(measurement["peak_rss_bytes"], RSS_LIMIT, measurement)
        print(f"SC-007 {operation}: peak_rss_bytes={measurement['peak_rss_bytes']}")

    def test_helper_job_uses_bounded_memory_for_two_gib_sparse_archive(self):
        self._assert_bounded_worker("helper")

    def test_promote_transfer_and_publication_use_bounded_memory(self):
        self._assert_bounded_worker("promote")


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in {"helper", "promote"}:
    raise SystemExit(_worker())
elif __name__ == "__main__":
    unittest.main()
