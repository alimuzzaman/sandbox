import math
import unittest


class JobModelTests(unittest.TestCase):
    def test_new_ids_are_32_hex_and_legacy_ids_remain_readable(self):
        from sandbox.jobs.models import new_job_id, validate_job_id

        job_id = new_job_id()
        self.assertEqual(len(job_id), 32)
        self.assertEqual(validate_job_id(job_id), job_id)
        self.assertEqual(validate_job_id("0123456789abcdef"), "0123456789abcdef")
        for value in ("", "xyz", "A" * 32, "0" * 31, "0" * 33):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_job_id(value)

    def test_argv_is_explicit_nonempty_text_without_nul(self):
        from sandbox.jobs.models import validate_argv

        self.assertEqual(validate_argv(["npm", "test"]), ("npm", "test"))
        for value in (None, [], [""], ["ok", "bad\x00arg"], "npm test"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_argv(value)

    def test_deadline_precedence_and_fallback_reminder(self):
        from sandbox.jobs.models import ExecutionProfile, resolve_deadline

        profile = ExecutionProfile("unit", 1800, 300, 20)
        explicit = resolve_deadline(explicit_seconds=45, profile=profile)
        self.assertEqual((explicit.seconds, explicit.source, explicit.reminder), (45, "explicit", None))
        fallback = resolve_deadline(profile=profile)
        self.assertEqual((fallback.seconds, fallback.source), (1800, "profile:unit"))
        self.assertIn("profile", fallback.reminder)

    def test_invalid_and_unbounded_deadlines_fail(self):
        from sandbox.jobs.models import resolve_deadline

        for value in (0, -1, True, math.inf, math.nan, 604801, "forever"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_deadline(explicit_seconds=value)
        with self.assertRaises(ValueError):
            resolve_deadline()

    def test_lifecycle_transitions_and_terminal_immutability(self):
        from sandbox.jobs.models import Lifecycle, validate_transition

        validate_transition(Lifecycle.ACCEPTED, Lifecycle.QUEUED)
        validate_transition(Lifecycle.QUEUED, Lifecycle.RUNNING)
        validate_transition(Lifecycle.RUNNING, Lifecycle.SUCCEEDED)
        for current, target in (
            (Lifecycle.ACCEPTED, Lifecycle.SUCCEEDED),
            (Lifecycle.SUCCEEDED, Lifecycle.RUNNING),
            (Lifecycle.CANCELLED, Lifecycle.FAILED),
        ):
            with self.subTest(current=current, target=target), self.assertRaises(ValueError):
                validate_transition(current, target)

    def test_submission_digest_is_stable_and_secret_values_are_not_serialized(self):
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        values = dict(
            kind="test", project_root="/tmp/project", project_identity="project-1",
            target_kind="remote", remote_name="vps", workspace_label="unit",
            argv=("npm", "test"), deadline_seconds=60,
            source=SourceIdentity("sha256:source", commit="abc", dirty_digest="def"),
            environment_keys=("TOKEN",),
        )
        first = JobSubmission(**values)
        second = JobSubmission(**values)
        self.assertEqual(first.canonical_digest(), second.canonical_digest())
        self.assertNotIn("secret-value", first.canonical_json())
        self.assertIn('"TOKEN"', first.canonical_json())


if __name__ == "__main__":
    unittest.main()
