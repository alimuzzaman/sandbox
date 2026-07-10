# Remote Hermes Agent

`sb hermes` operates a host-native Hermes Agent installation on an explicitly
configured Sandbox remote. Hermes runs as the same remote account as Sandbox,
so it can execute the remote `sb` CLI and receives the complete Sandbox MCP
catalog over local stdio.

## Trust boundary

This is a trusted single-operator profile. Full Sandbox MCP access includes
destructive tools; Hermes terminal approvals are set to manual and dangerous
cron terminal actions are denied, but MCP access is not a separate
authorization boundary. Ask for explicit user intent before deleting, resetting,
restoring, exposing, or otherwise mutating Sandbox resources.

Secrets, provider credentials, OAuth values, and Git tokens are never passed as
`sb hermes` arguments and must never be printed. Use remote interactive device
authentication for Git providers; do not copy a workstation private SSH key to
the remote.

## V1 workflow

```bash
./sb hermes doctor --remote scaleway-sandbox --json
./sb hermes install --remote scaleway-sandbox --version v2026.7.7.2 --json
./sb hermes setup --remote scaleway-sandbox

./sb hermes repo auth github --remote scaleway-sandbox
./sb hermes repo clone --remote scaleway-sandbox --url git@github.com:OWNER/REPO.git --name repo
./sb hermes run --remote scaleway-sandbox --repo repo --prompt "Inspect the test command" --async --json
```

Every coding invocation creates an isolated worktree by default. Use
`--no-worktree` only when deliberately working in the primary checkout. Dirty
or active worktrees are never removed automatically.

For WordPress repositories, Hermes must call `ensure_instance` with its active
worktree as `project_dir` before using instance-scoped Sandbox tools. A generic
Git repository never creates a WordPress instance implicitly.

## Gateway and MCP controls

Gateway commands require a non-empty explicit `--allow` list:

```bash
./sb hermes gateway install --remote scaleway-sandbox --allow user-or-channel
./sb hermes gateway start --remote scaleway-sandbox --allow user-or-channel
```

The local Sandbox MCP server also exposes `hermes_status(remote)` and
`hermes_run(remote, repo, prompt, worktree=true, async_=true)`. Async runs
return a Sandbox job ID that can be inspected or cancelled with the existing
`async_job_status` and `async_job_kill` tools.

## V2 and V3 gates

V2 adds update/rollback, backup/restore, resource limits, cleanup, health, and
reboot recovery. The upstream web dashboard is V3 work and is blocked until
real V2 acceptance evidence exists. Default dashboard access will be loopback
over an authenticated SSH tunnel; public OAuth/TLS exposure is separately
planned and never uses insecure mode.
