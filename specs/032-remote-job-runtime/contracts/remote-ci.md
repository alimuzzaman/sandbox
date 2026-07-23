# Contract: Remote CI Compatibility

## Scope

Remote CI v1 runs compatible GitHub Actions workflow graphs through `act` on one
selected provisioned Linux remote. It provides useful development/CI evidence; it does
not claim GitHub-hosted runner equivalence.

## Preflight input

```json
{
  "project_dir": "/project",
  "remote": "scaleway-sandbox",
  "workspace": "ci",
  "workflow": ".github/workflows/test.yml",
  "event": "pull_request",
  "jobs": ["test"],
  "inputs": {"suite": "unit"},
  "accepted_differences": ["act.job-timeout-ignored"],
  "safe_mode": true
}
```

Workflow path must remain under the project. Preflight resolves YAML and expressions
only to the extent required to classify the graph and compatibility; it must not run a
workflow step, deploy source, create a workspace, fetch secrets, or mutate a remote.

## Preflight result

```json
{
  "ok": false,
  "compatible": false,
  "engine": {"name": "act", "version": "observed-version"},
  "catalog_version": "2",
  "runner": {"platform": "linux", "accepted": true},
  "graph": {
    "jobs": ["build", "test"],
    "selected_jobs": ["test"],
    "matrix_cells": 4,
    "dependencies": {"test": ["build"]}
  },
  "differences": [
    {
      "id": "act.job-timeout-ignored",
      "location": "jobs.test.timeout-minutes",
      "severity": "block",
      "accepted": false,
      "message": "Sandbox must enforce the outer job deadline"
    }
  ],
  "safe_mode_actions": [],
  "blocking": ["act.job-timeout-ignored"]
}
```

Execution is allowed only when `blocking` is empty. Accepted differences remain in the
final result.

## Initial compatibility catalog

The versioned detector must at least classify currently documented `act` differences:

- ignored workflow/job concurrency
- ignored run name
- discarded step summaries
- ignored problem matchers and annotations
- incomplete GitHub context
- incomplete run-step cancellation
- ignored job permissions
- ignored job timeout (Sandbox outer deadline is still mandatory)
- ignored job continue-on-error
- Node availability differences for JavaScript actions
- unavailable OpenID Connect URL
- ignored deployment environment and environment-scoped secrets
- unsupported Docker context
- non-Linux `runs-on`

Catalog entries state whether Sandbox compensates, blocks, warns, or requires explicit
acceptance. Version/behavior probes supplement static detection but may not silently
remove a documented difference.

## Safe mode

Safe mode is on by default and classifies steps/actions as test/build/cache/artifact or
deployment/release/publish/external-mutation. It blocks or neutralizes the latter before
execution and records an explicit semantic difference. Secret names may be passed only
from configured allowlists; production credentials are never forwarded by default.

Unknown external mutation is a blocking preflight result, not a warning. A user asking
to run development CI does not authorize deployment or release behavior.

## Execution model

- Root parent job: workflow submission and aggregate outcome.
- Child job: each selected GitHub Actions job.
- Matrix child: each expanded cell, with isolated workspace/runtime.
- Step events: retained as structured annotations within the child output when the act
  output can identify them; raw retained output remains complete.
- Dependencies: child starts only after required predecessors satisfy workflow policy.
- Capacity: active cells are bounded by host capacity/max-parallel; excess cells queue.
- Timeout: Sandbox outer deadlines are authoritative even where `act` ignores
  `timeout-minutes`.
- Retry: creates new attempts and preserves prior cells/results.
- Artifacts: workflow artifact requests are mapped to Sandbox constrained artifacts only
  for literal project-relative paths. Preflight blocks globs/expressions, unsupported
  upload options, and missing-file semantics other than explicit `if-no-files-found: error`
  using named compatibility differences before execution.

## Final result

The terminal parent persists a normalized result while preserving compatibility keys
`aggregate`, `children`, and `result_json`. It includes engine/version where available,
workflow/source identity, selected graph, accepted differences, safe-mode skips, each
original child/cell references and outcomes, output completeness, artifact/difference
counts, cleanup policy/state, and aggregate conclusion within a 256 KiB persisted cap. Full
current child detail remains in `children`; retries retain the actual CI parent but appear
separately in `retry_attempts`. Aggregate-parent retry is explicitly unsupported, and prior
terminal membership/results remain immutable. Aggregate
success requires all required children to pass plus complete Sandbox finalization.

An `act` zero exit is insufficient when output storage failed, a required artifact was
unsafe/missing, the deadline elapsed, cancellation was incomplete, or cleanup policy
failed in a way declared fatal.

