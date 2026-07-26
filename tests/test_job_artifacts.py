import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.jobs.artifacts import ArtifactError, _copy_exact, collect
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class ArtifactTests(unittest.TestCase):
    def test_regular_project_file_is_collected_and_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "result.txt").write_text("result")
            repo = JobRepository(root / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission("test", str(root), "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(root, free_disk_reserve=0); storage.job_dir(job["job_id"], create=True)
            items = collect(storage, repo, job["job_id"], project_root=root, declared_paths=("result.txt",))
            self.assertEqual(items[0]["display_name"], "result.txt")
            with self.assertRaises(ArtifactError): collect(storage, repo, job["job_id"], project_root=root, declared_paths=("../escape",))
            repo.close()

    def test_symlink_artifact_is_rejected_even_when_target_stays_under_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "real.txt").write_text("result")
            (root / "link.txt").symlink_to(root / "real.txt")
            repo = JobRepository(root / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission("test", str(root), "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(root, free_disk_reserve=0); storage.job_dir(job["job_id"], create=True)
            with self.assertRaisesRegex(ArtifactError, "symlink"):
                collect(storage, repo, job["job_id"], project_root=root, declared_paths=("link.txt",))
            repo.close()

    def test_literal_directory_is_collected_as_deterministic_bounded_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "reports"
            (report / "nested").mkdir(parents=True)
            (report / "z.txt").write_text("z")
            (report / "nested" / "a.txt").write_text("a")
            repo = JobRepository(root / "jobs.sqlite")
            storage = JobStorage(root, free_disk_reserve=0)
            archives = []
            for workspace in ("one", "two"):
                job, _ = repo.accept(JobSubmission("test", str(root), workspace, "local", workspace,
                    ("echo", "x"), 60, SourceIdentity("s")))
                storage.job_dir(job["job_id"], create=True)
                item = collect(storage, repo, job["job_id"], project_root=root,
                               declared_paths=("reports",))[0]
                self.assertEqual(item["kind"], "archive")
                archive = storage.job_dir(job["job_id"]) / repo.snapshot(job["job_id"])["artifacts"][0]["stored_relative_path"]
                archives.append(archive.read_bytes())
                with tarfile.open(archive, "r") as handle:
                    self.assertEqual(handle.getnames(), ["reports", "reports/nested", "reports/nested/a.txt", "reports/z.txt"])
            self.assertEqual(archives[0], archives[1])
            repo.close()

    def test_directory_rejects_symlink_fifo_and_entry_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "reports"
            report.mkdir()
            (report / "real.txt").write_text("result")
            (report / "link.txt").symlink_to(report / "real.txt")
            repo = JobRepository(root / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission("test", str(root), "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(root, free_disk_reserve=0); storage.job_dir(job["job_id"], create=True)
            with self.assertRaisesRegex(ArtifactError, "symlink"):
                collect(storage, repo, job["job_id"], project_root=root, declared_paths=("reports",))
            (report / "link.txt").unlink()
            fifo = report / "pipe"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ArtifactError, "regular file or directory"):
                collect(storage, repo, job["job_id"], project_root=root, declared_paths=("reports",))
            fifo.unlink()
            (report / "second.txt").write_text("second")
            with patch("sandbox.jobs.artifacts.MAX_ARCHIVE_ENTRIES", 1):
                with self.assertRaisesRegex(ArtifactError, "entry count limit"):
                    collect(storage, repo, job["job_id"], project_root=root, declared_paths=("reports",))
            with patch("sandbox.jobs.artifacts.MAX_ARTIFACT_BYTES", 3):
                with self.assertRaisesRegex(ArtifactError, "size limit"):
                    collect(storage, repo, job["job_id"], project_root=root, declared_paths=("reports",))
            repo.close()

    def test_service_artifact_query_rejects_negative_zero_boolean_and_oversized_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "result.txt").write_text("result")
            repo = JobRepository(root / "jobs.sqlite")
            storage = JobStorage(root, free_disk_reserve=0)
            service = JobService(repo, storage, None, launcher=lambda _: None)
            job, _ = repo.accept(JobSubmission("test", str(root), "p", "local", "w",
                ("echo", "x"), 60, SourceIdentity("s")))
            storage.job_dir(job["job_id"], create=True)
            artifact = collect(storage, repo, job["job_id"], project_root=root,
                               declared_paths=("result.txt",))[0]
            invalid = ((-1, 1), (False, 1), (0, 0), (0, False), (0, 1_048_577))
            for offset, maximum in invalid:
                with self.subTest(offset=offset, maximum=maximum):
                    with self.assertRaisesRegex(ValueError, "artifact (offset|page bytes)"):
                        service.get_artifact(job["job_id"], artifact["artifact_id"],
                                             offset=offset, max_bytes=maximum)
            repo.close()

    def test_cleanup_expires_collected_artifact_and_prevents_later_retrieval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "report.txt").write_text("retained result")
            repo = JobRepository(root / "jobs.sqlite")
            storage = JobStorage(root, free_disk_reserve=0)
            service = JobService(repo, storage, None, launcher=lambda _: None)
            job, _ = repo.accept(JobSubmission("test", str(project), "p", "local", "w",
                ("echo", "x"), 60, SourceIdentity("s")))
            storage.job_dir(job["job_id"], create=True)
            artifact = collect(storage, repo, job["job_id"], project_root=project,
                               declared_paths=("report.txt",))[0]
            self.assertEqual(service.get_artifact(job["job_id"], artifact["artifact_id"]),
                             b"retained result")
            repo.transition(job["job_id"], "running")
            repo.transition(job["job_id"], "succeeded", exit_code=0)
            service.cleanup(job["job_id"], logs=False, artifacts=True, metrics=False)
            self.assertEqual(repo.snapshot(job["job_id"])["artifacts"][0]["status"], "expired")
            with self.assertRaisesRegex(RuntimeError, "artifact_unavailable"):
                service.get_artifact(job["job_id"], artifact["artifact_id"])
            repo.close()

    def test_exact_copy_detects_growth_and_shrink(self):
        with self.assertRaisesRegex(ArtifactError, "grew"):
            _copy_exact(io.BytesIO(b"abcd"), io.BytesIO(), 3)
        with self.assertRaisesRegex(ArtifactError, "shrank"):
            _copy_exact(io.BytesIO(b"ab"), io.BytesIO(), 3)

    def test_directory_archive_rejects_live_size_change_after_planning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); report = root / "reports"; report.mkdir()
            changing = report / "result.txt"; changing.write_text("one")
            repo = JobRepository(root / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission("test", str(root), "p", "local", "w",
                ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(root, free_disk_reserve=0); storage.job_dir(job["job_id"], create=True)
            from sandbox.jobs import artifacts as artifact_module
            original = artifact_module._archive_entries

            def plan_then_grow(*args, **kwargs):
                entries = original(*args, **kwargs)
                changing.write_text("one-but-now-larger")
                return entries

            with patch("sandbox.jobs.artifacts._archive_entries", side_effect=plan_then_grow):
                with self.assertRaisesRegex(ArtifactError, "changed during collection"):
                    collect(storage, repo, job["job_id"], project_root=root,
                            declared_paths=("reports",))
            repo.close()
