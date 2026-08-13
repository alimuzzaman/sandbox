from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from sandbox.resources.adapters import LocalResourceAdapter
from sandbox.resources.adapters import _parse_byte_size
from sandbox.resources.models import CleanupCandidate, ResourceObservation
from sandbox.resources.plans import PlanStore
from sandbox.resources.service import ResourceService
from sandbox.services.process import ProcessResult
from tests.resource_fixtures import NOW, deep_attribution


class FakeRunner:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def run(self, argv, *, cwd=None, env=None, timeout=None):
        command = tuple(argv)
        self.calls.append((command, timeout))
        for prefix, response in self.responses.items():
            if command[:len(prefix)] == prefix:
                return response(command) if callable(response) else response
        return ProcessResult(command, 127, "", "unavailable")


def response(stdout="", returncode=0, stderr=""):
    return ProcessResult((), returncode, stdout, stderr)


class TestLocalResourceAdapter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / "deploy-src" / "project-workspace-deadbeef").mkdir(parents=True)
        (self.home / "runtime" / "dl-cache").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_build_cache_byte_sizes_accept_raw_and_human_formats(self):
        self.assertEqual(_parse_byte_size("4096"), 4096)
        self.assertEqual(_parse_byte_size("1.5GB"), 1610612736)
        self.assertEqual(_parse_byte_size("2 MiB"), 2097152)
        self.assertIsNone(_parse_byte_size("unknown"))

    def test_fast_scan_is_read_only_and_marks_unmeasured_stale_paths_unverified(self):
        runner = FakeRunner()
        adapter = LocalResourceAdapter(
            self.home, runner=runner, clock=lambda: NOW, host_root=self.home,
        )
        snapshot = adapter.observe(thorough=False, budget_seconds=15)
        worktree = next(item for item in snapshot.resources if item.kind == "worktree")
        self.assertEqual(worktree.classification, "unverified")
        self.assertEqual(worktree.size_state, "not_measured")
        destructive = {
            ("docker", "container", "rm"),
            ("docker", "volume", "rm"),
            ("docker", "network", "rm"),
        }
        self.assertFalse(any(
            command[:3] in destructive
            for command, _timeout in runner.calls
        ))

    def test_pre_cancelled_observation_runs_no_provider_commands(self):
        runner = FakeRunner()
        adapter = LocalResourceAdapter(
            self.home, runner=runner, clock=lambda: NOW, host_root=self.home,
        )

        snapshot = adapter.observe(
            thorough=True, deep=True, budget_seconds=30, cancelled=True,
        )

        self.assertIsNotNone(snapshot.capacity)
        self.assertEqual(snapshot.resources, ())
        self.assertEqual(runner.calls, [])
        self.assertEqual(snapshot.category_outcomes, ({
            "category": "resource_measurement",
            "status": "cancelled",
            "reason": "request_cancelled_before_collection",
        },))

    def test_pre_cancelled_observation_serializes_as_ok_cancelled(self):
        runner = FakeRunner()
        adapter = LocalResourceAdapter(
            self.home, runner=runner, clock=lambda: NOW, host_root=self.home,
        )
        service = ResourceService(adapter, PlanStore(self.home / "plans"))

        result = service.status(
            thorough=True, deep=True, budget_seconds=30, cancelled=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["target"]["kind"], "local")
        self.assertIn("capacity", result["data"])
        self.assertEqual(runner.calls, [])

    def test_thorough_timeout_is_explicit_and_never_zero(self):
        runner = FakeRunner({
            ("du", "-sk"): response(returncode=124, stderr="process timed out"),
        })
        adapter = LocalResourceAdapter(
            self.home, runner=runner, clock=lambda: NOW, host_root=self.home,
        )
        snapshot = adapter.observe(thorough=True, budget_seconds=15)
        worktree = next(item for item in snapshot.resources if item.kind == "worktree")
        self.assertEqual(worktree.size_state, "timed_out")
        self.assertIsNone(worktree.size_bytes)
        self.assertEqual(worktree.reclaimable_bytes, 0)

    def test_deep_observation_attaches_bounded_partial_attribution(self):
        mount = str(self.home)
        runner = FakeRunner({
            ("df", "-Pk"): response(
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                f"/dev/fixture 100 80 20 80% {mount}\n",
            ),
            ("docker", "info"): response(returncode=127),
            ("du", "-x"): response(
                f"20\t{mount}/deploy-src\n", returncode=124,
                stderr="process timed out",
            ),
            ("docker", "system", "df"): response(returncode=127),
            ("du", "-sk"): response("1\t/path\n"),
        })
        adapter = LocalResourceAdapter(
            self.home, runner=runner, clock=lambda: NOW, host_root=self.home,
        )
        snapshot = adapter.observe(
            thorough=True, deep=True, budget_seconds=30,
        )
        self.assertIsNotNone(snapshot.deep_attribution)
        self.assertEqual(snapshot.deep_attribution.status, "partial")
        self.assertEqual(
            snapshot.deep_attribution.reconciliation.directory_allocated_bytes,
            20 * 1024,
        )
        self.assertEqual(
            snapshot.capacity_scope_id,
            snapshot.deep_attribution.capacity_scope_id,
        )
        self.assertTrue(all(
            timeout is None or 0 < timeout <= 30
            for _command, timeout in runner.calls
        ))

    def test_deep_managed_roots_are_typed_stable_and_not_public_evidence(self):
        workspace = self.home / "deploy-src" / "project-workspace-deadbeef"
        adapter = LocalResourceAdapter(
            self.home,
            registry_records=lambda: {
                "fixture": {
                    "root": str(workspace),
                    "instance": "fixture-password=not-public",
                },
            },
            job_resource_records=lambda: {
                "jobs": [{
                    "project_root": str(workspace),
                    "job_id": "job-token=not-public",
                    "lifecycle": "running",
                }],
                "artifacts": [],
            },
            clock=lambda: NOW,
            host_root=self.home,
        )

        roots = adapter._deep_managed_roots()

        self.assertEqual(roots, tuple(sorted(
            roots, key=lambda item: (item["path"], item["kind"], item["owner_id"]),
        )))
        matching = [
            item for item in roots
            if item["path"] == str(workspace.resolve(strict=False))
        ]
        self.assertEqual({item["kind"] for item in matching}, {
            "registry_root", "job_root",
        })
        self.assertTrue(all(
            item["owner_id"].startswith("managed_root-")
            for item in matching
        ))
        self.assertTrue(all(
            "password" not in json.dumps(item)
            and "token" not in json.dumps(item)
            for item in roots
        ))

    def test_deep_collector_failure_returns_explicit_partial_evidence(self):
        class RaisingCollector:
            def __init__(self, *_args, **_kwargs):
                pass

            def collect(self, **_kwargs):
                raise RuntimeError("password=do-not-expose")

        adapter = LocalResourceAdapter(
            self.home,
            runner=FakeRunner(),
            clock=lambda: NOW,
            host_root=self.home,
            deep_collector_factory=RaisingCollector,
        )

        snapshot = adapter.observe(
            thorough=True, deep=True, budget_seconds=30,
        )

        self.assertIsNotNone(snapshot.deep_attribution)
        self.assertEqual(snapshot.deep_attribution.status, "partial")
        coverage = snapshot.deep_attribution.coverage
        self.assertEqual(coverage[0].category, "deep_collection")
        self.assertEqual(coverage[0].status, "unavailable")
        self.assertEqual(coverage[0].reason, "deep_collector_failed")
        self.assertNotIn("password", json.dumps(snapshot.deep_attribution.to_dict()))
        outcome = next(item for item in snapshot.category_outcomes
                       if item["category"] == "deep_attribution")
        self.assertEqual(outcome["status"], "partial")

    def test_deep_hands_managed_roots_and_capacity_snapshots_to_collector(self):
        workspace = self.home / "deploy-src" / "project-workspace-deadbeef"
        received = {}

        class CapturingCollector:
            def __init__(self, *_args, **_kwargs):
                pass

            def collect(self, **kwargs):
                received.update(kwargs)
                return deep_attribution()

        runner = FakeRunner({
            ("df", "-Pk"): response(
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                f"/dev/fixture 100 80 20 80% {self.home}\n",
            ),
        })
        adapter = LocalResourceAdapter(
            self.home,
            runner=runner,
            registry_records=lambda: {
                "fixture": {"root": str(workspace)},
            },
            job_resource_records=lambda: {
                "jobs": [{"project_root": str(workspace)}],
                "artifacts": [],
            },
            clock=lambda: NOW,
            host_root=self.home,
            deep_collector_factory=CapturingCollector,
        )

        snapshot = adapter.observe(
            thorough=True, deep=True, budget_seconds=30,
        )

        self.assertIsNotNone(snapshot.deep_attribution)
        self.assertEqual(received["capacity_snapshots"][str(self.home)], {
            "total_bytes": 100 * 1024,
            "used_bytes": 80 * 1024,
            "available_bytes": 20 * 1024,
        })
        self.assertEqual(
            {item["kind"] for item in received["managed_roots"]},
            {"registry_root", "job_root"},
        )
        self.assertTrue(all(
            item["owner_id"].startswith("managed_root-")
            for item in received["managed_roots"]
        ))

    def test_pre_snapshot_failure_does_not_discard_collector_evidence(self):
        received = {}

        class SnapshotFailureRunner(FakeRunner):
            def run(self, argv, *, cwd=None, env=None, timeout=None):
                if tuple(argv) == ("df", "-Pk"):
                    raise RuntimeError("unexpected parser failure")
                return super().run(argv, cwd=cwd, env=env, timeout=timeout)

        class CapturingCollector:
            def __init__(self, *_args, **_kwargs):
                pass

            def collect(self, **kwargs):
                received.update(kwargs)
                return deep_attribution()

        adapter = LocalResourceAdapter(
            self.home,
            runner=SnapshotFailureRunner(),
            clock=lambda: NOW,
            host_root=self.home,
            deep_collector_factory=CapturingCollector,
        )

        snapshot = adapter.observe(
            thorough=True, deep=True, budget_seconds=30,
        )

        self.assertEqual(snapshot.deep_attribution.status, "complete")
        self.assertEqual(received["capacity_snapshots"], {})

    def test_deep_budget_is_recomputed_after_capacity_snapshot(self):
        now = [0.0]
        received = {}

        class SnapshotRunner(FakeRunner):
            def run(self, argv, *, cwd=None, env=None, timeout=None):
                if tuple(argv) == ("df", "-Pk"):
                    now[0] = 2.0
                    return response(
                        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                        f"/dev/fixture 100 80 20 80% {self_home}\n",
                    )
                return super().run(argv, cwd=cwd, env=env, timeout=timeout)

        class CapturingCollector:
            def __init__(self, *_args, **_kwargs):
                pass

            def collect(self, **kwargs):
                received.update(kwargs)
                return deep_attribution()

        self_home = self.home
        adapter = LocalResourceAdapter(
            self.home,
            runner=SnapshotRunner(),
            clock=lambda: NOW,
            host_root=self.home,
            deep_collector_factory=CapturingCollector,
        )

        with patch("sandbox.resources.adapters.time.monotonic", lambda: now[0]):
            adapter.observe(thorough=False, deep=True, budget_seconds=10)

        self.assertEqual(received["budget_seconds"], 8.0)

    def test_expired_budget_does_not_start_deep_collector(self):
        now = [0.0]
        collector_started = []
        calls_before_snapshot = []

        class SnapshotRunner(FakeRunner):
            def run(self, argv, *, cwd=None, env=None, timeout=None):
                if tuple(argv) == ("df", "-Pk"):
                    calls_before_snapshot.append(len(self.calls))
                    now[0] = 1.0
                    return response(
                        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                        f"/dev/fixture 100 80 20 80% {self_home}\n",
                    )
                return super().run(argv, cwd=cwd, env=env, timeout=timeout)

        class UnexpectedCollector:
            def __init__(self, *_args, **_kwargs):
                collector_started.append(True)

            def collect(self, **_kwargs):
                self.fail("collector must not start after deadline")

        self_home = self.home
        adapter = LocalResourceAdapter(
            self.home,
            runner=SnapshotRunner(),
            clock=lambda: NOW,
            host_root=self.home,
            deep_collector_factory=UnexpectedCollector,
        )

        with patch("sandbox.resources.adapters.time.monotonic", lambda: now[0]):
            snapshot = adapter.observe(thorough=False, deep=True, budget_seconds=1)

        self.assertEqual(collector_started, [])
        self.assertEqual(len(adapter.runner.calls), calls_before_snapshot[0])
        self.assertEqual(snapshot.deep_attribution.status, "partial")
        coverage = snapshot.deep_attribution.coverage[0]
        self.assertEqual(coverage.status, "timed_out")
        self.assertEqual(coverage.reason, "overall_budget_exhausted")

    def test_owned_unmounted_volume_requires_private_measurement_before_stale(self):
        containers = []
        volumes = [{
            "Name": "sandbox-fixture_modules",
            "Labels": {"com.docker.compose.project": "sandbox-fixture"},
            "Mountpoint": "/var/lib/docker/volumes/sandbox-fixture_modules/_data",
        }]
        networks = []
        runner = FakeRunner({
            ("docker", "ps", "-aq"): response(""),
            ("docker", "volume", "ls", "-q"): response("sandbox-fixture_modules\n"),
            ("docker", "volume", "inspect"): response(json.dumps(volumes)),
            ("docker", "network", "ls", "-q"): response(""),
            ("pgrep", "-xo", "dockerd"): response("123\n"),
            ("sudo", "-n", "nsenter"): response("4096\t/data\n"),
            ("du", "-sk"): response("1\t/path\n"),
        })
        adapter = LocalResourceAdapter(
            self.home, runner=runner, clock=lambda: NOW, host_root=self.home,
        )
        snapshot = adapter.observe(thorough=True, budget_seconds=30)
        volume = next(item for item in snapshot.resources if item.kind == "volume")
        self.assertEqual(volume.classification, "stale_candidate")
        self.assertEqual(volume.size_bytes, 4096 * 1024)
        self.assertIn("compose_project_label", volume.evidence)

    def test_network_lifecycle_requires_live_evidence_and_preserves_foreign_rows(self):
        networks = [
            {
                "Id": "managed-idle",
                "Name": "sandbox-fixture_default",
                "Labels": {"com.docker.compose.project": "sandbox-fixture"},
                "Containers": {},
            },
            {
                "Id": "managed-active",
                "Name": "sandbox-fixture_active",
                "Labels": {"com.docker.compose.project": "sandbox-fixture"},
                "Containers": {"container": {}},
            },
            {
                "Id": "foreign-active",
                "Name": "customer-net",
                "Labels": {"com.docker.compose.project": "customer"},
                "Containers": {"container": {}},
            },
            {
                "Id": "unattributed-idle",
                "Name": "unattributed-net",
                "Labels": {},
                "Containers": {},
            },
        ]
        runner = FakeRunner({
            ("docker", "ps", "-aq"): response(""),
            ("docker", "volume", "ls", "-q"): response(""),
            ("docker", "network", "ls", "-q"): response(
                "managed-idle\nmanaged-active\nforeign-active\n"
                "unattributed-idle\n",
            ),
            ("docker", "network", "inspect"): response(json.dumps(networks)),
            ("docker", "image", "ls", "-q"): response(""),
            ("docker", "buildx", "du"): response(""),
        })
        adapter = LocalResourceAdapter(
            self.home, runner=runner, clock=lambda: NOW, host_root=self.home,
        )
        observed = {
            item.locator: item
            for item in adapter.observe(thorough=False, budget_seconds=30).resources
            if item.kind == "network"
        }
        self.assertEqual(observed["managed-idle"].classification, "unverified")
        self.assertEqual(observed["managed-idle"].reclaimable_bytes, 0)
        self.assertEqual(observed["managed-active"].classification, "active")
        self.assertEqual(observed["foreign-active"].owner_kind, "foreign")
        self.assertEqual(observed["foreign-active"].classification, "active")
        self.assertEqual(observed["unattributed-idle"].owner_kind, "unmanaged")
        self.assertEqual(observed["unattributed-idle"].classification, "unmanaged")
        self.assertTrue(all(not item.capacity_accounted for item in observed.values()))
        self.assertFalse(any(
            command[:3] == ("docker", "network", "rm")
            for command, _timeout in runner.calls
        ))

    def test_adapter_contains_no_broad_prune_command(self):
        source = Path(__file__).parent.parent.joinpath(
            "sandbox/resources/adapters.py",
        ).read_text()
        self.assertNotIn("docker system prune", source)
        self.assertNotIn("docker volume prune", source)
        for broad_kind in ("system", "volume", "image", "network", "container"):
            self.assertNotIn(
                f'("docker", "{broad_kind}", "prune"',
                source,
            )
        self.assertIn('"--filter", f"id={candidate.locator}"', source)

    def test_registry_references_protect_stopped_engine_resources(self):
        volumes = [{
            "Name": "sandbox-permanent_db",
            "Labels": {"com.docker.compose.project": "sandbox-permanent"},
            "Mountpoint": "/var/lib/docker/volumes/sandbox-permanent_db/_data",
        }]
        runner = FakeRunner({
            ("docker", "ps", "-aq"): response(""),
            ("docker", "volume", "ls", "-q"): response("sandbox-permanent_db\n"),
            ("docker", "volume", "inspect"): response(json.dumps(volumes)),
            ("docker", "network", "ls", "-q"): response(""),
            ("docker", "image", "ls", "-q"): response(""),
            ("du", "-sk"): response("1\t/path\n"),
        })
        adapter = LocalResourceAdapter(
            self.home,
            runner=runner,
            registry_records=lambda: {
                "fixture": {
                    "root": "/tmp/permanent",
                    "instance": "permanent",
                },
            },
            clock=lambda: NOW,
            host_root=self.home,
        )
        item = next(
            resource for resource in adapter.observe(
                thorough=True,
                budget_seconds=30,
            ).resources
            if resource.kind == "volume"
        )
        self.assertEqual(item.classification, "retained")
        self.assertIn("instance_registry", item.references)

    def test_retained_job_reference_protects_workspace(self):
        workspace = self.home / "deploy-src" / "project-workspace-deadbeef"
        runner = FakeRunner({
            ("du", "-sk"): response("1\t/path\n"),
        })
        adapter = LocalResourceAdapter(
            self.home,
            runner=runner,
            job_resource_records=lambda: {
                "jobs": [{
                    "project_root": str(workspace),
                    "lifecycle": "succeeded",
                    "cleanup_policy": "retain",
                    "cleanup_state": "retained",
                }],
                "artifacts": [],
            },
            clock=lambda: NOW,
            host_root=self.home,
        )
        item = next(
            resource for resource in adapter.observe(
                thorough=True,
                budget_seconds=30,
            ).resources
            if resource.kind == "worktree"
        )
        self.assertEqual(item.classification, "retained")
        self.assertIn("retained_job", item.references)

    def test_retained_job_reference_protects_matching_workspace_volume(self):
        workspace = self.home / "deploy-src" / "project-workspace-deadbeef"
        volumes = [{
            "Name": "sandbox-project-workspace-deadbeef_modules",
            "Labels": {
                "com.docker.compose.project":
                "sandbox-project-workspace-deadbeef",
            },
            "Mountpoint": "/private/volume",
        }]
        runner = FakeRunner({
            ("docker", "ps", "-aq"): response(""),
            ("docker", "volume", "ls", "-q"): response(
                "sandbox-project-workspace-deadbeef_modules\n",
            ),
            ("docker", "volume", "inspect"): response(json.dumps(volumes)),
            ("docker", "network", "ls", "-q"): response(""),
            ("docker", "image", "ls", "-q"): response(""),
            ("docker", "buildx", "du"): response(""),
            ("du", "-sk"): response("1\t/path\n"),
        })
        adapter = LocalResourceAdapter(
            self.home,
            runner=runner,
            job_resource_records=lambda: {
                "jobs": [{
                    "project_root": str(workspace),
                    "lifecycle": "succeeded",
                    "cleanup_policy": "retain",
                    "cleanup_state": "retained",
                }],
                "artifacts": [],
            },
            clock=lambda: NOW,
            host_root=self.home,
        )
        item = next(
            resource for resource in adapter.observe(
                thorough=True, budget_seconds=30,
            ).resources
            if resource.kind == "volume"
        )
        self.assertEqual(item.classification, "retained")
        self.assertIn("instance_registry", item.references)

    def test_local_build_cache_is_visible_but_not_owned_by_name(self):
        record = {
            "ID": "a" * 24,
            "Size": "4096",
            "Reclaimable": True,
            "Mutable": False,
        }
        runner = FakeRunner({
            ("docker", "ps", "-aq"): response(""),
            ("docker", "volume", "ls", "-q"): response(""),
            ("docker", "network", "ls", "-q"): response(""),
            ("docker", "image", "ls", "-q"): response(""),
            ("docker", "buildx", "du"): response(json.dumps(record) + "\n"),
            ("du", "-sk"): response("1\t/path\n"),
        })
        adapter = LocalResourceAdapter(
            self.home, runner=runner, clock=lambda: NOW, host_root=self.home,
        )
        item = next(
            resource for resource in adapter.observe(
                thorough=True, budget_seconds=30,
            ).resources
            if resource.kind == "build_cache"
        )
        self.assertEqual(item.size_bytes, 4096)
        self.assertEqual(item.classification, "unverified")
        self.assertEqual(item.reclaimable_bytes, 0)

    def test_only_expired_terminal_job_artifact_is_disposable(self):
        artifact = self.home / "runtime" / "jobs" / "job-1" / "artifacts" / "a1"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"1234")
        runner = FakeRunner({
            ("du", "-sk"): response("1\t/path\n"),
        })
        adapter = LocalResourceAdapter(
            self.home,
            runner=runner,
            job_resource_records=lambda: {
                "jobs": [],
                "artifacts": [{
                    "artifact_id": "a1",
                    "job_id": "job-1",
                    "stored_relative_path": "artifacts/a1",
                    "display_name": "report",
                    "size_bytes": 4,
                    "expires_at": "2026-07-27T00:00:00Z",
                    "status": "available",
                    "job_lifecycle": "succeeded",
                }],
            },
            clock=lambda: NOW,
            host_root=self.home,
        )
        item = next(
            resource for resource in adapter.observe(
                thorough=False,
                budget_seconds=15,
            ).resources
            if resource.kind == "job_artifact"
        )
        self.assertEqual(item.classification, "disposable_cache")
        self.assertEqual(item.reclaimable_bytes, 4)
        self.assertIn("job_registry", item.evidence)

    def test_exact_image_delete_uses_only_planned_identifier(self):
        current = response()

        class Adapter(LocalResourceAdapter):
            def _find_current(self, candidate):
                return candidate_observation

        runner = FakeRunner({
            ("docker", "image", "rm"): current,
        })
        candidate_observation = ResourceObservation(
            resource_id="image-1",
            kind="image",
            locator="sha256:abc",
            display_name="fixture:latest",
            owner_kind="project",
            owner_id="sandbox-fixture",
            classification="disposable_cache",
            size_state="measured",
            size_bytes=100,
            reclaimable_bytes=100,
            evidence=("compose_project_label",),
        )
        adapter = Adapter(
            self.home, runner=runner, clock=lambda: NOW, host_root=self.home,
        )
        outcome = adapter.remove(
            CleanupCandidate.from_observation(candidate_observation),
        )
        self.assertEqual(outcome.status, "removed")
        self.assertIn(
            (("docker", "image", "rm", "sha256:abc"), 60),
            runner.calls,
        )


if __name__ == "__main__":
    unittest.main()
