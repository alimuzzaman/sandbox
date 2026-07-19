# Lenzora Remote Test Job Issues - 2026-07-19

## Context

While running Lenzora verification through Sandbox remote jobs on `scaleway-sandbox`, several Sandbox runtime issues made remote test evidence unreliable or hard to interpret.

Source repo under test: `/Users/alim/Sites/git/lenzora`
Remote target: `scaleway-sandbox`
Representative workspace labels: `lenzora-targeted-026-029`, earlier ad hoc Lenzora test workspaces

The latest submitted job at the time of writing is:

- `acf342667ddac8c9551425d750e9b454`
- Submitted with `sb exec --remote scaleway-sandbox --workspace lenzora-targeted-026-029 --timeout 3600 --detach -- ...`
- `job-status` shows `target: {"kind":"remote","remote":"scaleway-sandbox","workspace":"lenzora-targeted-026-029"}` and lifecycle `running`
- The same status payload also shows `target_kind: "local"` and `remote_name: null`, which is confusing even though the target object indicates a remote job

## Issues Found

### 1. Remote job status reports contradictory target fields

Observed status JSON includes both:

```json
{
  "target": {
    "kind": "remote",
    "remote": "scaleway-sandbox",
    "workspace": "lenzora-targeted-026-029"
  },
  "target_kind": "local",
  "remote_name": null
}
```

Expected behavior:

- `target_kind` should agree with `target.kind`, or be removed/deprecated from user-facing JSON.
- `remote_name` should be populated for remote jobs, or the schema should make clear that `target.remote` is authoritative.

Impact:

- Operators cannot easily prove that a test ran remotely.
- Agents may waste time rechecking remote state instead of doing product work.

### 2. Compose health checks fail before the test command reaches Jest

Several detached remote jobs failed during `sb ensure`/Compose health before the actual `jest` command ran. The declared health check in Lenzora checks for `node_modules/.bin/jest`, so dependency installation and service health are tightly coupled.

Representative symptoms:

- Job exits nonzero before any Jest suite output appears.
- Compose/service health is reported as failed even when the intended command includes a dependency install step.
- Increasing health retries locally in the tested repo improved patience but does not solve the underlying orchestration ambiguity.

Expected behavior:

- For `sb exec --remote ... -- sh -lc 'pnpm install && pnpm exec jest ...'`, Sandbox should either:
  - allow the command's own install step to satisfy runtime readiness before health is enforced, or
  - provide a clear documented pattern for dependency bootstrap in generic Compose projects with persistent `node_modules` volumes.

Impact:

- The job can fail before executing the requested test command.
- Users see a remote-test failure that is actually a Sandbox readiness/bootstrap failure.

### 3. Persistent workspace can lose or mask dependency state

Some reruns in the same remote workspace failed with `jest not found`, while later runs showed only `pnpm install` output and no Jest evidence despite exit code `0`.

Latest deterministic reproduction in workspace
`lenzora-targeted-026-029-healthfix`:

- Job `4dd3904292e69dbded1a24990d25fecf` reached Compose readiness and completed
  `pnpm install` (`Done in 1m 45.3s`) against the declared persistent
  `lenzora-sandbox-node-modules` volume, but exited `1` without any Jest output.
- The immediately following job `8d20a0774f6f1b627abee504049736ef`
  targeted the same remote workspace and invoked `pnpm exec jest` directly.
  Compose again reported `status: ready`, then failed in four seconds with
  `ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL Command "jest" not found`.
- Both status payloads identify `target.kind` as `remote` while reporting
  top-level `target_kind: local` and `remote_name: null`.

Observed patterns:

- `pnpm exec jest ...` reports `jest not found` after earlier jobs had installed dependencies.
- A command shaped as `pnpm install ... && pnpm exec jest ...` sometimes returns output containing only install lines, with no visible Jest output.

Expected behavior:

- If the command exits `0`, retained output should include enough evidence to prove the full command chain executed.
- If `pnpm exec jest` is skipped, not found, or never reached, the job should preserve that as a clear failure.
- Persistent workspace dependency volumes should behave consistently across remote runs or be explicitly invalidated/reset.

Impact:

- A green exit without Jest output is not trustworthy test evidence.
- Agents can enter retry loops trying to distinguish product failures from Sandbox workspace state issues.

### 4. Process exit can be marked failed after tests pass because of late teardown output

One targeted Lenzora job produced substantive Jest evidence:

- `PASS tests/contract/api/cli-operation-catalog.contract.test.ts`
- `PASS tests/integration/api/comparison-scope.integration.test.ts`
- `PASS tests/integration/api/cli-command-families.integration.test.ts`
- 3 suites passed, 9 tests passed

But the Sandbox job still exited nonzero after a late Prisma/Jest teardown warning similar to:

- `Cannot log after tests are done`
- Prisma warning about `libssl` detection

Expected behavior:

- Sandbox should preserve both facts distinctly:
  - the test process reported passing suites
  - the final process exited nonzero because of late stderr/teardown behavior
- Documentation should recommend the safest way to run Jest in this environment, such as `--runInBand --forceExit` only when appropriate.

Impact:

- The useful test evidence is mixed with an infrastructure/runtime teardown failure.
- Operators cannot confidently classify the result without reading raw logs.

### 5. Remote Docker network capacity can fail unrelated jobs

Some remote attempts failed with Docker address pool exhaustion.

Expected behavior:

- Sandbox should detect stale zero-container networks created by prior jobs and offer scoped cleanup guidance.
- The failure should identify whether it is safe to clean only Sandbox-owned resources for a workspace.

Impact:

- New remote jobs fail before project setup.
- Manual cleanup is risky unless the operator can distinguish Sandbox-owned networks from user resources.

### 6. Same-process install reaches a Jest-ready sentinel but loses all Jest output

A bounded follow-up removed cross-job dependency reuse from the equation:

- Remote job: `adb9f90ee2585c9cef245b7b91836894`
- Remote workspace: `lenzora-026-029-verification-20260719`
- The single shell process ran `pnpm install`, verified
  `node_modules/.bin/jest` was executable, and printed
  `SANDBOX_JEST_READY` immediately before `pnpm exec jest`.
- The job then exited `1` after roughly five minutes.
- Retained stdout contains Compose readiness only.
- Retained stderr contains the completed install and `SANDBOX_JEST_READY`, but
  no Jest banner, suite name, assertion failure, process signal, or shell error.
- Status again reports `target.kind: remote` alongside top-level
  `target_kind: local` and `remote_name: null`.

This proves the dependency binary existed in the same remote process and the
shell reached the line immediately before Jest. The remaining failure is not
explained by persistent-volume reuse or a missing dependency.

Expected behavior:

- Preserve stdout/stderr and termination evidence for the child command after
  the sentinel, including process signals, OOM/cgroup termination, or command
  exit diagnostics.
- Do not replace the child failure with a truncated Python `RuntimeError`
  whose `detail` begins midway through earlier dependency-install progress.

Impact:

- Product failures cannot be diagnosed because no Jest output survives.
- Re-running the same command is not useful and risks an agent retry loop.

## Suggested Acceptance Criteria

A Sandbox agent fixing this should aim for:

- Remote job status JSON has one unambiguous source of truth for target kind and remote name.
- Generic Compose `sb exec --remote` can run dependency bootstrap plus test commands without failing readiness before the command has a chance to run.
- Persistent workspace dependency state is deterministic, or reset/rebuild behavior is explicit and easy to invoke.
- Exit code, command output, and test evidence make it clear whether Jest actually ran.
- Docker network exhaustion errors include safe, scoped cleanup instructions or automatic cleanup for Sandbox-owned stale networks.

## Useful Commands

Current-style Lenzora remote test command:

```sh
sb exec --remote scaleway-sandbox \
  --workspace lenzora-targeted-026-029 \
  --timeout 3600 \
  --detach -- \
  sh -lc 'corepack enable && pnpm install --config.engine-strict=false --frozen-lockfile=false && pnpm exec jest --runInBand --runTestsByPath tests/contract/api/cli-operation-catalog.contract.test.ts tests/integration/api/cli-command-families.integration.test.ts tests/integration/api/comparison-scope.integration.test.ts tests/integration/cli/cli-auth.integration.test.ts'
```

Status inspection:

```sh
sb job-status acf342667ddac8c9551425d750e9b454 --remote scaleway-sandbox --json
sb job-output acf342667ddac8c9551425d750e9b454 --remote scaleway-sandbox --tail-bytes 20000
```
