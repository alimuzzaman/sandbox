"""Regressions from the local WAL job-evidence acceptance observations."""

import hashlib
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from sandbox.delivery.trace_models import TraceContractError, TraceQueryBudget
from sandbox.jobs import _trace_snapshot
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import (
    JobRepository, read_delivery_job_evidence, read_trace_job_evidence,
)


@unittest.skipUnless(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'pread') and
                     hasattr(sqlite3.Connection, 'deserialize'),
                     'Owner snapshot format requires POSIX reads and SQLite deserialize')
class JobTraceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'registry.sqlite3'
        self.control = self.root / 'control'
        self.control.mkdir()
        self.repository = JobRepository(self.path)
        self.addCleanup(self.repository.close)
        self.repository.connection.execute('PRAGMA wal_autocheckpoint=0')
        item = JobSubmission(
            kind='exec', project_root=str(self.control), project_identity='project-fixture',
            target_kind='local', workspace_label='default', deadline_seconds=60,
            request_id='snapshot-owner-prepare',
            argv=('/usr/bin/node', str(self.control / 'node_modules/tsx/dist/cli.mjs'),
                  str(self.control / 'scripts/hosted-deployment/worker.ts'),
                  'prepare', str(self.root / 'private-run')),
            source=SourceIdentity('sha256:' + 'a' * 64, 'b' * 40),
        )
        row, _ = self.repository.accept(item)
        self.job_id = row['job_id']
        self.repository.transition(self.job_id, 'running')
        self.repository.put_process_identity(
            self.job_id, host_boot_id='fixture-boot', supervisor_pid=100,
            supervisor_start_identity='fixture-supervisor', supervisor_nonce_hash='c' * 64,
            child_pid=101, child_pgid=101, child_start_identity='fixture-child',
        )
        self.binding = {
            'role': 'prepare_job', 'request_id': item.request_id,
            'control_root_digest': 'sha256:' + hashlib.sha256(str(self.control).encode()).hexdigest(),
            'control_source_commit': 'b' * 40,
        }

    def trace(self, path=None, budget=None):
        return read_trace_job_evidence(
            path or self.path, self.job_id, **self.binding,
            budget=budget or TraceQueryBudget(time.monotonic() + 5),
        )

    def readers(self, path=None):
        return self.trace(path), read_delivery_job_evidence(path or self.path, self.job_id)

    def fingerprints(self, directory=None):
        directory = directory or self.root
        return {path.name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in directory.iterdir() if path.is_file()}

    def assert_known(self, result, lifecycle='running'):
        self.assertEqual(result['state'], 'known', result)
        self.assertEqual(result['job']['job_id'], self.job_id)
        self.assertEqual(result['job']['lifecycle'], lifecycle)

    def assert_partial(self, path):
        before = self.fingerprints(path.parent)
        for result in self.readers(path):
            self.assertEqual(result['state'], 'partial', result)
            self.assertIsNone(result['job'])
            self.assertEqual(result['reason'], 'job_evidence_unavailable')
        self.assertEqual(self.fingerprints(path.parent), before)

    def copied_owner(self, label):
        directory = self.root / label
        directory.mkdir()
        for suffix in ('', '-wal', '-shm'):
            (directory / ('registry.sqlite3' + suffix)).write_bytes(
                Path(str(self.path) + suffix).read_bytes())
        return directory / 'registry.sqlite3'

    def test_both_readers_serve_wal_only_rows_without_source_connections_or_writes(self):
        self.assertEqual(self.path.stat().st_size, 4096)
        self.assertGreater(Path(str(self.path) + '-wal').stat().st_size, 4096)
        before = self.fingerprints()
        real_connect = sqlite3.connect
        destinations = []

        def connect(database, *args, **kwargs):
            destinations.append(database)
            return real_connect(database, *args, **kwargs)

        with patch.object(sqlite3, 'connect', side_effect=connect):
            trace, legacy = self.readers()
        self.assert_known(trace)
        self.assert_known(legacy)
        self.assertEqual(destinations, [':memory:', ':memory:'])
        self.assertEqual(self.fingerprints(), before)
        self.assertEqual(trace['submission']['control_source_commit'], 'b' * 40)
        self.assertNotIn('argv', trace['submission'])
        self.assertNotIn('project_root', trace['job'])

    def test_uncommitted_change_is_invisible_then_committed_change_is_visible(self):
        owner = self.repository.connection
        owner.execute('BEGIN IMMEDIATE')
        owner.execute("UPDATE jobs SET lifecycle='succeeded',exit_code=0 WHERE job_id=?", (self.job_id,))
        before = self.fingerprints()
        for result in self.readers():
            self.assert_known(result)
        self.assertEqual(self.fingerprints(), before)
        owner.commit()
        before = self.fingerprints()
        for result in self.readers():
            self.assert_known(result, 'succeeded')
        self.assertEqual(self.fingerprints(), before)

    def test_reused_wal_suffix_does_not_replace_the_published_head(self):
        owner = self.repository.connection
        owner.execute('CREATE TABLE fixture_padding(value BLOB)')
        owner.execute('BEGIN IMMEDIATE')
        owner.executemany('INSERT INTO fixture_padding VALUES (?)', [(b'x' * 2048,) for _ in range(20)])
        owner.commit()
        owner.execute('PRAGMA wal_checkpoint(RESTART)').fetchone()
        owner.execute("UPDATE jobs SET output_completeness='complete' WHERE job_id=?", (self.job_id,))
        head = Path(str(self.path) + '-shm').read_bytes()[:48]
        page_size = struct.unpack('=H', head[14:16])[0]
        frame_count = struct.unpack('=I', head[16:20])[0]
        prefix = 32 + frame_count * (24 + page_size)
        self.assertGreater(Path(str(self.path) + '-wal').stat().st_size, prefix)
        before = self.fingerprints()
        trace, legacy = self.readers()
        self.assert_known(trace)
        self.assert_known(legacy)
        self.assertEqual(trace['job']['output_completeness'], 'complete')
        self.assertEqual(self.fingerprints(), before)

    def test_zero_wal_and_closed_base_do_not_create_or_modify_sidecars(self):
        self.repository.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        self.assertEqual(Path(str(self.path) + '-wal').stat().st_size, 0)
        before = self.fingerprints()
        for result in self.readers():
            self.assert_known(result)
        self.assertEqual(self.fingerprints(), before)
        self.repository.close()
        before = self.fingerprints()
        for result in self.readers():
            self.assert_known(result)
        self.assertEqual(self.fingerprints(), before)
        # SQLite may retain empty sidecars on close. A copied checkpointed
        # base independently exercises the genuinely absent-sidecar branch.
        directory = self.root / 'checkpointed-copy'
        directory.mkdir()
        path = directory / 'registry.sqlite3'
        path.write_bytes(self.path.read_bytes())
        before = self.fingerprints(directory)
        for result in self.readers(path):
            self.assert_known(result)
        self.assertEqual(self.fingerprints(directory), before)
        self.assertFalse(Path(str(path) + '-wal').exists())
        self.assertFalse(Path(str(path) + '-shm').exists())

    def test_selected_frame_corruption_and_short_prefix_fail_closed(self):
        for label in ('checksum', 'short-prefix'):
            with self.subTest(label=label):
                path = self.copied_owner(label)
                wal_path = Path(str(path) + '-wal')
                wal = bytearray(wal_path.read_bytes())
                if label == 'checksum':
                    wal[32 + 24] ^= 1
                else:
                    del wal[-7:]
                wal_path.write_bytes(wal)
                self.assert_partial(path)

    def test_bad_header_or_index_and_missing_shm_fail_closed(self):
        for label in ('header-checksum', 'index-copy', 'missing-shm'):
            with self.subTest(label=label):
                path = self.copied_owner(label)
                if label == 'missing-shm':
                    Path(str(path) + '-shm').unlink()
                else:
                    source = Path(str(path) + ('-wal' if label == 'header-checksum' else '-shm'))
                    data = bytearray(source.read_bytes())
                    data[24 if label == 'header-checksum' else 0] ^= 1
                    source.write_bytes(data)
                self.assert_partial(path)

    def test_final_symlink_and_nonregular_source_are_not_followed(self):
        directory = self.root / 'unsafe'
        directory.mkdir()
        path = directory / 'registry.sqlite3'
        path.symlink_to(self.path)
        self.assert_partial(path)
        path.unlink()
        if hasattr(os, 'mkfifo'):
            os.mkfifo(path)
            for result in self.readers(path):
                self.assertEqual(result['state'], 'partial')
                self.assertIsNone(result['job'])

    def test_oversized_source_is_partial_without_reading_its_body(self):
        directory = self.root / 'oversized'
        directory.mkdir()
        path = directory / 'registry.sqlite3'
        with path.open('wb') as handle:
            handle.truncate(_trace_snapshot.SOURCE_BYTES + 1)
        before = path.stat()
        with patch.object(_trace_snapshot.os, 'pread', side_effect=AssertionError('oversized body read')):
            for result in self.readers(path):
                self.assertEqual(result['state'], 'partial')
                self.assertIsNone(result['job'])
        after = path.stat()
        self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))
        self.assertEqual({item.name for item in directory.iterdir()}, {'registry.sqlite3'})

    def test_expired_shared_budget_stops_before_opening_owner_state(self):
        budget = TraceQueryBudget(time.monotonic() - 1)
        with patch.object(_trace_snapshot.os, 'open', side_effect=AssertionError('expired owner read')):
            with self.assertRaises(TraceContractError) as caught:
                self.trace(budget=budget)
        self.assertEqual(caught.exception.code, 'budget_exhausted')

    def test_missing_database_preserves_missing_result_without_creating_state(self):
        directory = self.root / 'missing'
        directory.mkdir()
        for result in self.readers(directory / 'registry.sqlite3'):
            self.assertEqual((result['state'], result['reason']), ('missing', 'job_missing'))
        self.assertEqual(list(directory.iterdir()), [])

    def autovacuum_owner(self, name):
        path = self.root / name
        owner = sqlite3.connect(path, isolation_level=None)
        self.addCleanup(owner.close)
        owner.execute('PRAGMA page_size=512')
        owner.execute('PRAGMA auto_vacuum=FULL')
        owner.execute('VACUUM')
        owner.execute('PRAGMA journal_mode=WAL')
        owner.execute('PRAGMA wal_autocheckpoint=0')
        owner.execute('CREATE TABLE payloads(value BLOB)')
        owner.execute('BEGIN IMMEDIATE')
        owner.executemany('INSERT INTO payloads VALUES (?)', [(b'a' * 400,) for _ in range(64)])
        owner.commit()
        owner.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        return path, owner

    def assert_private_matches_owner(self, path, owner):
        expected = owner.execute('SELECT count(*),sum(length(value)),min(value),max(value) FROM payloads').fetchone()
        expected_pages = owner.execute('PRAGMA page_count').fetchone()[0]
        before = self.fingerprints()
        private = _trace_snapshot.read_connection(path, budget=TraceQueryBudget(time.monotonic() + 5))
        try:
            actual = private.execute('SELECT count(*),sum(length(value)),min(value),max(value) FROM payloads').fetchone()
            self.assertEqual(tuple(actual), expected)
            self.assertEqual(private.execute('PRAGMA page_count').fetchone()[0], expected_pages)
            self.assertEqual(private.execute('PRAGMA query_only').fetchone()[0], 1)
        finally:
            private.close()
        self.assertEqual(self.fingerprints(), before)

    def test_committed_truncation_then_extension_preserves_current_page_contents(self):
        path, owner = self.autovacuum_owner('truncate-extend.sqlite3')
        grown_pages = owner.execute('PRAGMA page_count').fetchone()[0]
        owner.execute('DELETE FROM payloads WHERE rowid>4')
        shrunk_pages = owner.execute('PRAGMA page_count').fetchone()[0]
        self.assertLess(shrunk_pages, grown_pages)
        owner.execute('BEGIN IMMEDIATE')
        owner.executemany('INSERT INTO payloads VALUES (?)', [(b'z' * 400,) for _ in range(12)])
        owner.commit()
        self.assertGreater(owner.execute('PRAGMA page_count').fetchone()[0], shrunk_pages)
        self.assert_private_matches_owner(path, owner)

    def test_checkpointed_truncation_does_not_require_deleted_historical_base_pages(self):
        path, owner = self.autovacuum_owner('checkpointed-truncate.sqlite3')
        original_bytes = path.stat().st_size
        owner.execute("UPDATE payloads SET value=? WHERE rowid=1", (b'b' * 400,))
        owner.execute('DELETE FROM payloads WHERE rowid>4')
        owner.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
        self.assertLess(path.stat().st_size, original_bytes)
        self.assertGreater(Path(str(path) + '-wal').stat().st_size, 32)
        self.assert_private_matches_owner(path, owner)


if __name__ == '__main__':
    unittest.main()
