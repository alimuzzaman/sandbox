"""Sandbox test package. Importing it guards the runner process; see runner_guard.

It also drops the durable-job child context. A suite started by
``sb job-start`` inherits ``SANDBOX_DURABLE_*`` from its supervisor; left in
place, code under test would treat the runner as that job's child and refuse
with "selected durable job registry is unavailable". Tests that need a job
context set their own synthetic one.
"""

import os

from tests.runner_guard import install as _install_runner_guard

# Keep in step with sandbox.jobs.supervisor._CHILD_CONTEXT_KEYS (checked by
# tests.test_job_supervisor).
DURABLE_CHILD_CONTEXT_KEYS = (
    "SANDBOX_DURABLE_JOB_ID", "SANDBOX_DURABLE_EXECUTION_KIND",
    "SANDBOX_DURABLE_REQUEST_ID", "SANDBOX_DURABLE_PROJECT_IDENTITY",
    "SANDBOX_DURABLE_PROJECT_ROOT_DIGEST", "SANDBOX_DURABLE_SOURCE_IDENTITY",
    "SANDBOX_DURABLE_SOURCE_COMMIT", "SANDBOX_DURABLE_SOURCE_DIRTY_DIGEST",
)

for _key in DURABLE_CHILD_CONTEXT_KEYS:
    os.environ.pop(_key, None)

_install_runner_guard()
