import unittest
import tempfile
import threading
import time
import base64
import json
from pathlib import Path

from sandbox.jobs.models import (OutputQuery, normalize_output_page_bytes,
                                 normalize_output_wait_seconds)
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.output import JobOutputStore, OutputCursorError, _cursor, _parse_cursor
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class OutputCursorModelTests(unittest.TestCase):
    def test_v2_cursor_has_sequence_and_offset_and_accepts_v1(self):
        job_id = "a" * 32
        value = _cursor(job_id, "combined", 7, 13)
        payload = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        self.assertEqual(set(payload), {"v", "j", "s", "q", "o"})
        self.assertEqual(payload["v"], 2)
        self.assertEqual(_parse_cursor(value, job_id, "combined"), (7, 13))

        legacy = base64.urlsafe_b64encode(json.dumps(
            {"j": job_id, "s": "combined", "q": 7}, separators=(",", ":")
        ).encode()).decode().rstrip("=")
        self.assertEqual(_parse_cursor(legacy, job_id, "combined"), (7, 0))

        for malformed in (
            _cursor(job_id, "combined", 7, 13)[:-1] + "!",
            base64.urlsafe_b64encode(json.dumps(
                {"v": 2, "j": job_id, "s": "combined", "q": 7, "o": 1, "x": 2}
            ).encode()).decode().rstrip("="),
            base64.urlsafe_b64encode(json.dumps(
                {"v": 2, "j": job_id, "s": "combined", "q": True, "o": 0}
            ).encode()).decode().rstrip("="),
            base64.urlsafe_b64encode(json.dumps(
                {"v": 2.0, "j": job_id, "s": "combined", "q": 7, "o": 0}
            ).encode()).decode().rstrip("="),
        ):
            with self.assertRaises(OutputCursorError):
                _parse_cursor(malformed, job_id, "combined")

    def test_read_rejects_v2_cursor_reused_for_another_job_or_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            first, _ = repo.accept(JobSubmission(
                "test", "/p", "p", "local", "first", ("echo", "x"), 60,
                SourceIdentity("s")))
            second, _ = repo.accept(JobSubmission(
                "test", "/p", "p", "local", "second", ("echo", "x"), 60,
                SourceIdentity("s")))
            storage = JobStorage(temp, free_disk_reserve=0)
            storage.job_dir(first["job_id"], create=True)
            storage.job_dir(second["job_id"], create=True)
            first_output = JobOutputStore(storage, repo, first["job_id"])
            second_output = JobOutputStore(storage, repo, second["job_id"])
            first_output.append("stdout", b"first\n"); first_output.finish("stdout")
            first_output.append("stderr", b"error\n"); first_output.finish("stderr")
            second_output.append("stdout", b"second\n"); second_output.finish("stdout")

            # This is an emitted, valid v2 cursor, not merely a malformed-token
            # parser fixture. Reusing it must fail at the store boundary.
            cursor = first_output.read(OutputQuery(stream="stdout", max_bytes=1))["cursor"]
            with self.assertRaisesRegex(OutputCursorError, "invalid for this job and stream"):
                second_output.read(OutputQuery(stream="stdout", cursor=cursor))
            with self.assertRaisesRegex(OutputCursorError, "invalid for this job and stream"):
                first_output.read(OutputQuery(stream="stderr", cursor=cursor))
            repo.close()

    def test_only_one_position_selector_is_allowed(self):
        with self.assertRaises(ValueError):
            OutputQuery(cursor="abc", offset=0)
        self.assertEqual(OutputQuery(wait_seconds=20).wait_seconds, 20)

    def test_output_wait_normalizer_accepts_zero_and_rejects_non_whole_bounds(self):
        self.assertEqual(normalize_output_wait_seconds(0), 0)
        self.assertEqual(normalize_output_wait_seconds(20), 20)
        for value in (True, False, None, "1", 1.0, -1, 21):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "output wait must be between 0 and 20 seconds"):
                    normalize_output_wait_seconds(value)
                with self.assertRaisesRegex(ValueError, "output wait must be between 0 and 20 seconds"):
                    OutputQuery(wait_seconds=value)

    def test_output_page_normalizer_has_one_shared_remote_safe_bound(self):
        self.assertEqual(normalize_output_page_bytes(262144), 262144)
        for value in (True, False, None, "1", 0, -1, 262145):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "output page bytes must be between 1 and 262144"):
                    normalize_output_page_bytes(value)
                with self.assertRaisesRegex(ValueError, "output page bytes must be between 1 and 262144"):
                    OutputQuery(max_bytes=value)

    def test_line_tail_and_cursor_long_poll_read_retained_output(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission("test", "/p", "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(temp, free_disk_reserve=0); storage.job_dir(job["job_id"], create=True)
            output = JobOutputStore(storage, repo, job["job_id"])
            output.append("stdout", b"one\ntwo\nthree\n")
            page = output.read(OutputQuery(lines=2))
            self.assertEqual(page["data"], "two\nthree\n")
            cursor = output.read(OutputQuery()).get("cursor")
            thread = threading.Thread(target=lambda: (time.sleep(.05), output.append("stdout", b"four\n")))
            thread.start()
            waited = output.read(OutputQuery(cursor=cursor, wait_seconds=1))
            thread.join()
            self.assertEqual(waited["data"], "four\n")
            repo.close()

    def test_offset_tail_since_and_base64_select_retained_events(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission("test", "/p", "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(temp, free_disk_reserve=0); storage.job_dir(job["job_id"], create=True)
            output = JobOutputStore(storage, repo, job["job_id"])
            output.append("stdout", b"first\n", timestamp=10.0)
            output.append("stdout", b"second\n", timestamp=20.0)
            self.assertEqual(output.read(OutputQuery(offset=2))["data"], "rst\nsecond\n")
            self.assertEqual(output.read(OutputQuery(tail_bytes=7))["data"], "second\n")
            self.assertEqual(output.read(OutputQuery(since="1970-01-01T00:00:15Z"))["data"], "second\n")
            encoded = output.read(OutputQuery(since="15", encoding="base64"))
            self.assertEqual(encoded["data"], "c2Vjb25kCg==")
            with self.assertRaisesRegex(ValueError, "since"):
                OutputQuery(since="")
            repo.close()

    def test_one_hundred_cursor_resumes_never_repeat_a_retained_event(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission(
                "test", "/p", "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(temp, free_disk_reserve=0)
            storage.job_dir(job["job_id"], create=True)
            output = JobOutputStore(storage, repo, job["job_id"])
            for number in range(100):
                output.append("stdout", f"{number}\n".encode())

            cursor = None
            sequences = []
            for _ in range(100):
                page = output.read(OutputQuery(cursor=cursor, max_events=1) if cursor else
                                   OutputQuery(max_events=1))
                sequences.extend(event["sequence"] for event in page["events"])
                cursor = page["cursor"]

            self.assertEqual(sequences, list(range(100)))
            self.assertEqual(len(sequences), len(set(sequences)))
            repo.close()

    def test_capped_pages_resume_event_suffix_without_duplicate_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission(
                "test", "/p", "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(temp, free_disk_reserve=0)
            storage.job_dir(job["job_id"], create=True)
            output = JobOutputStore(storage, repo, job["job_id"])
            output.append("stdout", b"abcdefghij"); output.finish("stdout")
            output.append("stdout", b"tail"); output.finish("stdout")

            cursor = None
            pages = []
            for _ in range(4):
                page = output.read(OutputQuery(max_bytes=4, cursor=cursor) if cursor
                                   else OutputQuery(max_bytes=4))
                pages.append(page)
                cursor = page["cursor"]

            self.assertEqual("".join(page["data"] for page in pages), "abcdefghijtail")
            self.assertEqual(pages[0]["events_read"], 1)
            self.assertEqual(pages[1]["events"], [])
            self.assertEqual(pages[2]["events_read"], 1)
            self.assertEqual(pages[3]["events"], [])
            self.assertTrue(pages[0]["has_more"])
            self.assertTrue(pages[1]["has_more"])
            self.assertTrue(pages[2]["has_more"])
            self.assertFalse(pages[3]["has_more"])
            repo.close()

    def test_partial_cursor_long_poll_returns_retained_suffix_immediately(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission(
                "test", "/p", "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(temp, free_disk_reserve=0)
            storage.job_dir(job["job_id"], create=True)
            output = JobOutputStore(storage, repo, job["job_id"])
            output.append("stdout", b"abcdefgh"); output.finish("stdout")
            first = output.read(OutputQuery(max_bytes=3))
            started = time.monotonic()
            suffix = output.read(OutputQuery(cursor=first["cursor"], max_bytes=32, wait_seconds=2))
            elapsed = time.monotonic() - started
            self.assertEqual(suffix["data"], "defgh")
            self.assertLess(elapsed, 0.5)
            self.assertEqual(suffix["events"], [])
            repo.close()

    def test_paged_base64_redacted_output_reassembles_without_secret_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission(
                "test", "/p", "p", "local", "w", ("echo", "x"), 60,
                SourceIdentity("s")))
            storage = JobStorage(temp, free_disk_reserve=0)
            storage.job_dir(job["job_id"], create=True)
            output = JobOutputStore(storage, repo, job["job_id"], secrets=["secret-value"])
            output.append("stdout", b"prefix secret-value suffix\n"); output.finish("stdout")
            output.append("stdout", b"tail secret-value end\n"); output.finish("stdout")

            full = output.read(OutputQuery(encoding="base64", max_bytes=1024))
            expected = base64.b64decode(full["data"], validate=True)
            self.assertEqual(expected, b"prefix [REDACTED] suffix\ntail [REDACTED] end\n")
            self.assertNotIn(b"secret-value", expected)

            cursor = None
            pages = []
            while True:
                page = output.read(OutputQuery(encoding="base64", max_bytes=5, cursor=cursor)
                                   if cursor else OutputQuery(encoding="base64", max_bytes=5))
                page_bytes = base64.b64decode(page["data"], validate=True)
                self.assertNotIn(b"secret-value", page_bytes)
                pages.append(page_bytes)
                cursor = page["cursor"]
                if not page["has_more"]:
                    break
            self.assertEqual(b"".join(pages), expected)
            for path in output.directory.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"secret-value", path.read_bytes())
            repo.close()
