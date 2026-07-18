import stat
import tempfile
import unittest


class JobContractTests(unittest.TestCase):
    def test_value_objects_reject_control_characters_and_unsafe_workspace(self):
        from sandbox.jobs.models import SourceIdentity, TargetRequest

        for value in ("../escape", "bad label", "bad\x00label", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TargetRequest("/tmp/project", workspace=value)
        with self.assertRaises(ValueError):
            SourceIdentity("secret\nvalue")

    def test_output_and_artifact_queries_are_bounded(self):
        from sandbox.jobs.models import ArtifactQuery, OutputQuery

        self.assertEqual(OutputQuery().max_bytes, 65536)
        self.assertEqual(ArtifactQuery("artifact-1").max_bytes, 1048576)
        for value in (0, -1, 262145):
            with self.subTest(value=value), self.assertRaises(ValueError):
                OutputQuery(max_bytes=value)
        with self.assertRaises(ValueError):
            ArtifactQuery("../artifact")

    def test_execution_policy_does_not_accept_arbitrary_filter_commands(self):
        from sandbox.jobs.models import OutputProfile

        profile = OutputProfile("agent", mode="sampled", every_lines=20,
                                include=("FAIL",), max_bytes=4096)
        self.assertEqual(profile.every_lines, 20)
        with self.assertRaises(TypeError):
            OutputProfile("bad", mode="smart", command="grep FAIL")

    def test_submission_json_uses_environment_names_and_source_identity_only(self):
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        job = JobSubmission(
            kind="exec", project_root="/tmp/project", project_identity="p",
            target_kind="remote", remote_name="vps", workspace_label="default",
            argv=("env",), deadline_seconds=30, environment_keys=("API_TOKEN",),
            source=SourceIdentity("sha256:abc"),
        )
        payload = job.as_dict()
        self.assertEqual(payload["environment_keys"], ["API_TOKEN"])
        self.assertNotIn("environment", payload)

    def test_storage_is_owner_only_atomic_and_contained(self):
        from sandbox.jobs.models import new_job_id
        from sandbox.jobs.storage import JobStorage

        with tempfile.TemporaryDirectory() as directory:
            storage = JobStorage(directory, free_disk_reserve=0)
            job_id = new_job_id()
            job_dir = storage.job_dir(job_id, create=True)
            target = storage.write_json_atomic(job_id, "spec.json", {"job_id": job_id})
            self.assertEqual(stat.S_IMODE(job_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertIn(job_id, target.read_text())
            with self.assertRaises(ValueError):
                storage.write_json_atomic(job_id, "../escape.json", {})

    def test_job_component_manifest_is_explicit_and_deterministic(self):
        from sandbox.jobs.manifest import builtin_job_component_registry

        components = {name: object() for name in (
            "repository", "storage", "process_identity", "clock", "profiles"
        )}
        registry = builtin_job_component_registry(**components)
        self.assertEqual([item.component_id for item in registry.specs()], [
            "repository", "storage", "process_identity", "clock", "profiles",
        ])


if __name__ == "__main__":
    unittest.main()
