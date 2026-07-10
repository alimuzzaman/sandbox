# CLI Contract: Remote Hermes Agent

## Conventions

- Every command requires an existing explicit `--remote NAME`; no implicit production remote is selected.
- Human output is concise and redacted. `--json` prints exactly one JSON object to stdout; diagnostics/log streams use stderr only when documented.
- Mutating update/restore/cleanup/public-exposure actions require `--confirm`. Interactive prompts are never substituted in JSON/non-interactive mode.
- Repository arguments are logical managed names, not arbitrary filesystem paths.
- V3 dashboard commands exist only after their implementation milestone, and every action first evaluates the V2 gate.

## Stable JSON envelope

```json
{
  "ok": true,
  "action": "status",
  "remote": "scaleway-sandbox",
  "version": "v2026.7.7.2",
  "commit": "<40-hex>",
  "status": "healthy",
  "repo": null,
  "path": null,
  "job_id": null,
  "data": {},
  "error": null
}
```

On failure, `ok` is false and `error` is:

```json
{
  "code": "stable_machine_code",
  "message": "sanitized actionable summary",
  "retryable": false,
  "details": {}
}
```

The envelope never contains secret values, repository URL userinfo, prompt text, raw SSH targets, or unbounded command output.

## V1 Commands

```text
./sb hermes install --remote NAME
                    [--version TAG] [--commit SHA] [--json]

./sb hermes setup --remote NAME [--portal] [--json]
./sb hermes doctor --remote NAME [--json]
./sb hermes status --remote NAME [--json]

./sb hermes repo auth github --remote NAME
./sb hermes repo clone URL --remote NAME [--name NAME] [--ref REF] [--json]
./sb hermes repo list --remote NAME [--json]

./sb hermes chat --remote NAME --repo NAME [--no-worktree]
./sb hermes run --remote NAME --repo NAME --prompt TEXT
                [--no-worktree] [--async] [--timeout SECONDS] [--json]

./sb hermes gateway setup --remote NAME
./sb hermes gateway install --remote NAME [--json]
./sb hermes gateway start|stop|restart|status --remote NAME [--json]
./sb hermes gateway logs --remote NAME [--lines N] [--json]
```

### Install

- Default version is the current integration-supported signed tag, initially `v2026.7.7.2`.
- The implementation resolves the tag to a full commit and verifies tag/commit consistency. A supplied `--commit` must match the supplied tag.
- It runs preflight before mutation, installs under the remote Sandbox user, and skips secret-bearing setup.
- Reinstalling the identical verified revision is a successful no-op/reconciliation.
- Installing a different revision after V1 requires the V2 update command; `install` does not become an update bypass.

### Setup

- Writes or reconciles only the Sandbox-managed Hermes profile/config section.
- `--portal` launches the upstream interactive provider flow; it is invalid with non-interactive JSON automation.
- Setup does not accept provider tokens as command-line arguments.
- Successful setup verifies direct `sb`, MCP initialization, complete catalog discovery, profile selection, and file permissions.

### Doctor/status

`status` is an observation with no repair. `doctor` runs bounded checks for reachability, platform, Git, Python/runtime, Docker access, disk, memory, paths, install revision, config, permissions, direct `sb`, MCP catalog, services, repositories, jobs, and release gates. It may recommend commands but performs no mutation.

### Repository commands

- `repo auth github` uses an interactive remote provider/device flow. It never copies a local private key or prints a token.
- `repo clone` accepts `https`, `ssh`, or provider shorthand only after parsing; URL userinfo is rejected.
- Omitted `--name` derives a validated final path segment. Existing destinations fail unless they already match the exact registered repository and origin, in which case the result is idempotent.
- `repo list` reports logical name, sanitized origin host/path, state, and active worktree count.

### Chat/run

- Worktree isolation is enabled by default. `--no-worktree` is an explicit per-invocation override and is reported prominently.
- `chat` requires a TTY and transfers terminal control through SSH.
- Synchronous `run` returns the bounded final result or a timeout error without abandoning an untracked process.
- `run --async` returns a Sandbox host-level job ID immediately. Existing `async-job` CLI/MCP operations poll or cancel it.
- `--prompt` is accepted for the requested V1 interface but must not be copied into logs/state. A future stdin/file option may reduce shell-history exposure without changing this contract.

### Gateway

- `setup` delegates provider-specific interactive configuration only after validating the final allowlist is non-empty and non-wildcard.
- `install` renders/validates the service and environment files before systemd mutation.
- Start/restart fails closed when the allowlist is missing or unsafe.
- Logs default to 200 lines and have a hard maximum; JSON logs are returned as a bounded sanitized string plus truncation metadata.

## V2 Commands

```text
./sb hermes update plan --remote NAME [--version TAG] [--json]
./sb hermes update apply --remote NAME --version TAG --confirm [--json]

./sb hermes backup create --remote NAME [--json]
./sb hermes backup list --remote NAME [--json]
./sb hermes backup restore ID --remote NAME --confirm [--json]

./sb hermes cleanup --remote NAME [--dry-run] [--confirm] [--json]
./sb hermes health --remote NAME [--json]
./sb hermes acceptance v2 --remote NAME [--json]
```

- `update plan` is read-only and reports current/target immutable commits, services affected, backup space, checks, and rollback.
- `update apply` rejects moving branch names and confirmation omission. It creates and verifies a backup before stopping services.
- `backup restore` verifies archive digest, compatibility, available space, and a pre-restore backup before replacement.
- `cleanup` defaults to dry-run. `--confirm` is required for removal, and dirty/active/ambiguous worktrees are never removed.
- `acceptance v2` executes the defined checks and records actual evidence; it has no option to directly set a passing status.

## V3 Dashboard Commands (after V2)

```text
./sb hermes dashboard install --remote NAME [--json]
./sb hermes dashboard setup --remote NAME [--port PORT] [--json]
./sb hermes dashboard start|stop|restart|status --remote NAME [--json]
./sb hermes dashboard logs --remote NAME [--lines N] [--json]
./sb hermes dashboard doctor --remote NAME [--json]

./sb hermes dashboard expose --remote NAME --fqdn HOST [--plan]
                             [--confirm] [--json]
./sb hermes dashboard unexpose --remote NAME [--plan] [--confirm] [--json]
```

- Every command first validates a current passing V2 gate. Before the gate, it returns `v2_gate_required` without mutation.
- Install adds upstream `web` and `pty` extras for the pinned revision; it does not create a custom frontend.
- Setup always renders loopback binding, `--no-open`, and TUI bridge support. `--insecure` is not accepted by the wrapper.
- Default access is an operator-created SSH tunnel to remote loopback; SSH is the authentication boundary.
- `expose --plan` is read-only. Public apply requires `--confirm`, feature 015 availability, explicit FQDN, supported upstream OAuth values stored outside version control, TLS, and authenticated health verification.
- Any public apply failure rolls back route/DNS state and stops the dashboard if safe authentication cannot be proven.
