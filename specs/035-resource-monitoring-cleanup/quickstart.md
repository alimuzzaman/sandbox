# Quickstart: Validate Resource Monitoring and Safe Cleanup

This guide validates the finished feature. Use only disposable resources for
mutating checks.

## Prerequisites

- A non-`main` checkout with feature 035 implemented.
- Python 3.10+ and the repository CLI environment.
- Docker available for engine-provider tests.
- A registered local Sandbox project for live read-only verification.
- Optional: a configured disposable remote for remote status verification.

## 1. Run focused automated checks

```bash
.cli-venv/bin/python -m unittest \
  tests.test_resource_adapters \
  tests.test_resource_service \
  tests.test_resource_interfaces \
  tests.test_command_composition \
  tests.test_mcp_composition
```

Expected:

- timeout and unavailable categories produce partial results;
- unknown or unmanaged resources are never candidates;
- cache plans exclude named volumes;
- stale plans require positive ownership and non-use evidence;
- target mismatch, expiry, replay, and missing confirmation are refused;
- a candidate that becomes active is skipped;
- CLI and MCP adapters preserve the shared result contract.

## 2. Inspect fast local status

```bash
./sb resources status --json
```

Expected:

- exit status zero when capacity is available;
- target is `local`;
- capacity uses raw byte fields;
- results state whether the scan is complete or partial;
- no file contents, credentials, or sensitive mount options appear;
- the command changes no resource state.

## 3. Inspect thorough local status

```bash
./sb resources status --thorough --budget 60 --json
```

Expected:

- the call completes within the selected overall budget plus bounded startup
  overhead;
- slow or inaccessible categories are explicit rather than zero;
- managed worktrees and volumes have evidence-backed classifications;
- unverified or unmanaged resources have zero reclaimable bytes.

## 4. Review safe-cache planning

```bash
./sb resources plan --scope cache --thorough --budget 60 --json
```

Expected:

- no resource changes;
- output includes plan ID, expiry, candidates, exclusions, and estimated bytes;
- running containers and every named volume are excluded;
- unmanaged host logs and package caches are monitoring-only.

Verify the plan is not executable without confirmation:

```bash
./sb resources cleanup --plan-id PLAN_ID --json
```

Expected: nonzero with `confirmation_required` and zero mutations.

## 5. Verify stale planning without mutation

```bash
./sb resources plan --scope stale --thorough --budget 90 --json
```

Expected:

- only positively owned unreferenced worktrees or volumes are candidates;
- name, age, or engine "dangling" state alone is insufficient;
- permanent hosts, active workspaces, retained jobs/backups, and ambiguous
  resources appear in exclusions.

## 6. Verify concurrent revalidation on a disposable fixture

1. Create a disposable, positively owned cache fixture.
2. Generate a cache plan and record its plan ID.
3. Make one candidate active after planning.
4. Confirm the plan:

```bash
./sb resources cleanup --plan-id PLAN_ID --confirm --json
```

Expected:

- the newly active candidate is skipped;
- other still-eligible disposable candidates receive itemized outcomes;
- final capacity and any drift are reported;
- rerunning the same plan is refused as already used.

Do not perform this scenario against permanent or ambiguous resources.

## 7. Verify named-remote status

```bash
./sb resources status --remote REMOTE_NAME --json
./sb resources status --remote REMOTE_NAME --thorough --budget 60 --json
./sb resources status --remote REMOTE_NAME --thorough --budget 300 --json
```

Expected:

- exact remote identity is present;
- no local host resources are mixed into the report;
- category timeouts remain partial remote results;
- monitoring does not deploy or upgrade the remote runtime;
- remote registry/job evidence comes through typed read-only repositories and
  incomplete evidence prevents persistent cleanup;
- host filesystem accounting does not double-count nested Docker/workspace
  detail.

## 8. Verify MCP parity

Invoke:

- `resource_status`
- `resource_cleanup_plan`
- `resource_cleanup_apply` without confirmation

against the same stable fixture used by the CLI checks.

Expected:

- target identity, classifications, candidates, exclusions, error codes, and
  confirmation behavior match the CLI;
- apply without confirmation is refused before any provider mutation.

## 9. Live done gate

Capture evidence from:

```bash
./sb resources status --json
./sb resources plan --scope cache --json
./sb status
```

The feature is done only when status and planning return valid live envelopes,
planning causes no mutation, and the existing instance remains healthy.

## 10. Workspace metadata/index ownership compatibility

Run the metadata migration in a disposable base or against an explicitly approved
metadata-only fixture. Do not use resource cleanup, network release, reset, or destroy.

```bash
./sb resources status --remote REMOTE_NAME --json
./sb workspace migrate --remote REMOTE_NAME --project-identity PROJECT_ID --json
./sb resources status --remote REMOTE_NAME --json
```

Verify:

- both status responses identify the same remote and retain stable resource IDs/counts;
- workspace bindings are typed by opaque `workspace_id`, include index generation and
  active lease/container/job references, and never expose direct paths or file contents;
- missing, unresolved, conflicting, duplicate, stale, or unavailable index evidence is
  `partial`/`unknown`/`indeterminate` with zero reclaimable bytes and no cleanup plan;
- a nested/malformed job-list response fails through the shared top-level decoder;
- moving metadata or a checkout locator does not release networks or change
  container/job/network counts; a fresh rescan is required before any plan.
