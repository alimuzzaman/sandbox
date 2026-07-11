# Remote Hermes Agent

`sb hermes` operates a host-native Hermes Agent installation on an explicitly
configured Sandbox remote. Hermes runs as the same remote account as Sandbox,
so it can execute the remote `sb` CLI and receives the complete Sandbox MCP
catalog over local stdio.

Setup persists the non-secret Sandbox MCP entry in Hermes's effective
`~/.hermes/config.yaml`: it passes the resolved `SANDBOX_HOME`, keeps calls
sequential, enables resource and prompt utilities, and deliberately sets no
tool include/exclude filter. `sb hermes doctor` confirms both that the entry is
registered by Hermes and that Hermes can initialize it. It also validates the
effective `sandbox` policy (resolved home, enabled resource/prompt utilities,
sequential calls, and no filters) using remote-only boolean checks; neither
check prints the remote configuration or provider credentials.

## Trust boundary

This is a trusted single-operator profile. Full Sandbox MCP access includes
destructive tools; Hermes terminal approvals are set to manual and dangerous
cron terminal actions are denied, but MCP access is not a separate
authorization boundary. Ask for explicit user intent before deleting, resetting,
restoring, exposing, or otherwise mutating Sandbox resources.

Secrets, provider credentials, OAuth values, and Git tokens are never passed as
`sb hermes` arguments and must never be printed. Use remote interactive device
authentication for Git providers; do not copy a workstation private SSH key to
the remote. GitHub device authentication requires `gh` on the remote; on the
supported Ubuntu host, install it once as the remote operator with
`sudo apt-get install -y gh`.

## V1 workflow

```bash
./sb hermes doctor --remote scaleway-sandbox --json
./sb hermes install --remote scaleway-sandbox --version v2026.7.7.2 --json
./sb hermes setup --remote scaleway-sandbox

./sb hermes repo auth github --remote scaleway-sandbox
./sb hermes repo clone --remote scaleway-sandbox --url git@github.com:OWNER/REPO.git --name repo
./sb hermes run --remote scaleway-sandbox --repo repo --prompt "Inspect the test command" --async --json
./sb hermes job status --remote scaleway-sandbox --job-id JOB_ID --json
```

Every coding invocation creates an isolated worktree by default under
`$SANDBOX_HOME/runtime/hermes-worktrees/<repository>/`, never inside the
primary checkout. Use `--no-worktree` only when deliberately working in the
primary checkout. Dirty or active worktrees are never removed automatically.
Worktree creation holds a repository-scoped remote advisory lock; detached
sessions retain their worktree path and terminal status so cleanup can
distinguish clean completed work from active work. Confirmed cleanup also
removes only completed job artifacts older than seven days; stale or dirty
sessions remain for review.
Managed clones initialize Git submodules recursively and pull Git LFS objects
when the remote has Git LFS available.

For WordPress repositories, Hermes must call `ensure_instance` with its active
worktree as `project_dir` before using instance-scoped Sandbox tools. A generic
Git repository never creates a WordPress instance implicitly.

## Gateway and MCP controls

Gateway setup requires a non-empty explicit `--allow` list. Lifecycle commands
reuse that stored allowlist and fail closed if it is missing or unsafe:

```bash
./sb hermes gateway setup --remote scaleway-sandbox --allow user-or-channel
./sb hermes gateway install --remote scaleway-sandbox
./sb hermes gateway start --remote scaleway-sandbox
```

Gateway installation enables systemd user lingering so an enabled gateway can
recover after a remote reboot; `hermes health` reports its linger state.

The local Sandbox MCP server also exposes `hermes_status(remote)` and
`hermes_run(remote, repo, prompt, worktree=true, async_=true)`, plus
`hermes_job_status(remote, job_id, offset=0)` and
`hermes_job_kill(remote, job_id)`. Async runs return a Hermes job ID; the
equivalent CLI operations are `sb hermes job status|kill`. Returned output is
bounded and sanitized.

## V2 and V3 gates

V2 adds update/rollback, backup/restore, resource limits, cleanup, health, and
reboot recovery. The upstream web dashboard is V3 work and is blocked until
real V2 acceptance evidence exists. Default dashboard access will be loopback
over an authenticated SSH tunnel; public OAuth/TLS exposure is separately
planned and never uses insecure mode.

```bash
./sb hermes policy show --remote scaleway-sandbox --json
./sb hermes policy set --remote scaleway-sandbox --max-jobs 2 --max-worktrees 8 \
  --min-free-disk-mb 1024 --min-free-memory-mb 512 --json
./sb hermes update plan --remote scaleway-sandbox --version v2026.7.7.2 --json
./sb hermes backup list --remote scaleway-sandbox --json
./sb hermes cleanup --remote scaleway-sandbox --dry-run --json
./sb hermes health --remote scaleway-sandbox --json
./sb hermes acceptance v2 --remote scaleway-sandbox --json
```

`update apply`, `backup restore`, and non-dry-run cleanup require `--confirm`.
`acceptance v2` is read-only: it reports the revision-bound evidence written by
the approved live recovery suite. It never offers an override or a way to set a
passing gate manually. Until every required check is recorded against the
currently installed Hermes commit, every dashboard action refuses without
changing the remote.

## Security and operating boundaries

- The Sandbox MCP catalog is intentionally unfiltered for this trusted,
  single-operator profile. It includes tools that can create containers,
  instances, files, and database state. Hermes approval prompts are a guardrail
  rather than a technical authorization boundary; destructive intent still
  needs explicit operator confirmation.
- Docker-equivalent access follows the existing remote Sandbox account. Do not
  add Hermes to a shared or multi-tenant account, and do not expose its local
  stdio MCP endpoint on the network.
- Provider device authentication, model credentials, gateway tokens, OAuth
  values, and private keys remain in the remote account's approved secret
  stores. Never place them in a repository URL, command argument, state file,
  backup, log, or result envelope. Hermes backups exclude authentication and
  session/checkpoint material.
- Prompts and response logs can contain sensitive repository context. Hermes
  stores no prompt body in integration state; job output is sanitized and
  bounded before it leaves the remote. Operators should remove completed job
  artifacts only after confirming they are no longer needed.
- The gateway requires an explicit non-wildcard allowlist. The dashboard is
  unavailable until a current V2 gate passes; when scheduled for V3 it must
  bind to loopback first, and any public OAuth/TLS route requires the reviewed
  managed-hosting workflow and a separate confirmation.
