# Contract: Hermes Scheduler CLI and MCP

## CLI

```text
./sb hermes health --remote NAME --json
./sb hermes worktree list --remote NAME --json
./sb hermes repo sync --remote NAME --repo sandbox --confirm --json
./sb hermes gateway converge --remote NAME [--confirm] --json
./sb hermes cron catalog --remote NAME --json
./sb hermes cron reconcile --remote NAME [--confirm] [--force-replace] --json
./sb hermes cron verify JOB_ID --remote NAME [--timeout SECONDS] --confirm --json
./sb hermes cron output JOB_ID --remote NAME [--lines N] --json
./sb hermes worktree inspect --remote NAME --name MANAGED_NAME --json
./sb hermes worktree preserve --remote NAME --name MANAGED_NAME [--confirm] --json
```

- Read-only commands never require confirmation and never reconcile sessions, record gates, or persist remote state.
- `gateway converge` without confirmation returns a plan.
- `cron reconcile` without confirmation returns a plan. The feature-025 migration uses `--force-replace`; ordinary later runs retain exact matches.
- `cron verify` requires confirmation, validates the route first, and returns only after terminal evidence or timeout.
- Every response uses the existing Hermes JSON envelope and includes stable `ok`, `action`, `status`, `remote`, `data`, and sanitized `error` fields.
- Partial mutations return `ok=false`, `status=partial`, completed steps, remaining desired entries, and recovery guidance.

## MCP

```text
hermes_health(remote)
hermes_worktree_list(remote)
hermes_repo_sync(remote, repo, confirm=false)
hermes_gateway_converge(remote, confirm=false)
hermes_cron_catalog(remote)
hermes_cron_reconcile(remote, confirm=false, force_replace=false)
hermes_cron_verify(remote, job_id, timeout=600, confirm=false)
hermes_cron_output(remote, job_id, lines=200)
hermes_worktree_inspect(remote, name)
hermes_worktree_preserve(remote, name, confirm=false)
```

MCP wrappers call the same CLI/service path; they do not implement remote behavior independently.

## Remote transport

- SSH and SCP calls share one endpoint-isolated, short-lived authenticated connection when supported by the local OpenSSH client.
- Reuse is opportunistic: missing, stale, or unsupported multiplexing state falls back to a normal secure connection without changing host-key or authentication policy.
- The control endpoint is owner-only, bounded in idle lifetime, and named with a one-way endpoint hash rather than a readable host, user, port, or credential.
- Reuse never combines confirmation authority. Each CLI/MCP operation retains its own timeout, exit status, output bounds, and redaction.

## Security and output bounds

- No stored prompt, environment value, request body, authorization material, or raw unbounded log is returned.
- Errors are classified and redacted before crossing the remote boundary.
- Worktree paths must remain inside registered managed roots.
- Cron output reads only a validated job ID's newest bounded outcome section;
  stored prompts are never returned and secret-like outcomes are withheld.
- Worktree inspection withholds secret-like diffs; preservation rejects
  untracked files, failed `git diff --check HEAD`, bearer/JWT credentials, and
  unexpected branches. Preservation rechecks the reviewed tree and branch while
  holding the same per-worktree lock used by scheduled writers.
- Repository synchronization smoke-tests a staged runtime before replacement;
  failed validation preserves the prior runtime and retained virtual environments.
- Destructive operations fail closed when dirty worktrees are unpreserved unless the operation cannot affect them (cron replacement itself records them but does not delete worktrees).
