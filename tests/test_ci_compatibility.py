import unittest

from sandbox.ci.compatibility import detect


class CompatibilityTests(unittest.TestCase):
    def test_catalogued_job_differences_have_exact_locations(self):
        result = detect({"concurrency": "one", "jobs": {"win": {"runs-on": "windows-latest", "timeout-minutes": 3, "permissions": {"contents": "read"}}}})
        self.assertEqual({item["id"] for item in result}, {"act.concurrency-ignored", "act.non-linux-runner", "act.job-timeout-ignored", "act.job-permissions-ignored"})

    def test_documented_act_catalogue_entries_are_detected_at_exact_locations(self):
        workflow = {
            "concurrency": "one", "run-name": "contract",
            "jobs": {"compat": {
                "runs-on": "windows-latest", "concurrency": "job-one",
                "permissions": {"contents": "read", "id-token": "write"},
                "timeout-minutes": 3, "continue-on-error": True,
                "environment": "production", "container": "node:20",
                "steps": [
                    {"run": "echo note >> $GITHUB_STEP_SUMMARY"},
                    {"run": "echo '::add-matcher::matcher.json'"},
                    {"run": "echo '${{ github.sha }}'"},
                    {"if": "cancelled()", "run": "echo cleanup"},
                ],
            }},
        }
        locations = {item["id"]: item["location"] for item in detect(workflow)}
        self.assertEqual(locations, {
            "act.concurrency-ignored": "jobs.compat.concurrency",
            "act.run-name-ignored": "run-name",
            "act.non-linux-runner": "jobs.compat.runs-on",
            "act.job-permissions-ignored": "jobs.compat.permissions",
            "act.oidc-unavailable": "jobs.compat.permissions.id-token",
            "act.job-timeout-ignored": "jobs.compat.timeout-minutes",
            "act.continue-on-error-ignored": "jobs.compat.continue-on-error",
            "act.environment-ignored": "jobs.compat.environment",
            "act.docker-context-unsupported": "jobs.compat.container",
            "act.step-summary-discarded": "jobs.compat.steps[0].run",
            "act.problem-matchers-ignored": "jobs.compat.steps[1].run",
            "act.github-context-incomplete": "jobs.compat.steps[2]",
            "act.cancellation-incomplete": "jobs.compat.steps[3]",
        })
