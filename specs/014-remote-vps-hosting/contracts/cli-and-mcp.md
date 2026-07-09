# Contracts: CLI + MCP surface — remote VPS hosting

## `./sb remote` subcommand group

| Command | Purpose |
|---|---|
| `./sb remote add <name> ssh://user@host[:port]` | Register a VPS target (stored in `sandbox.local.yml`'s `remotes:` block). Idempotent — re-running for an existing name updates it. |
| `./sb remote list` | Show configured remotes, each with reachability (a live SSH/Tailscale ping) and provisioned status. |
| `./sb remote provision <name>` | SSH in and run `scripts/install-remote.sh`: install/join Tailscale, install Docker CE + compose plugin, install the `sb` runtime, provision the `visit` tools venv, start the remote MCP server (streamable-HTTP, bound to the Tailscale interface only), mint a bearer token. Safe to re-run (spec FR-005). |
| `./sb remote up <name>` / `./sb remote down <name>` | Start/stop the remote MCP server process on the VPS (over SSH) — does NOT affect running WordPress instances or the Tailscale connection itself. |
| `./sb remote remove <name>` | Forget the remote locally. MUST NOT touch anything on the VPS itself (spec FR-003) — existing instances there keep running; the user is told this explicitly in the confirmation message. |

Exit codes: `0` on success; `1` on any failure, with a human-readable `die()`-style
message naming the actual cause (unreachable host, SSH auth failure, install-step
failure) — never a bare stack trace.

## `./sb deploy` command

```
./sb deploy --project-dir DIR --remote NAME [--json]
```

| Flag | Purpose |
|---|---|
| `--project-dir DIR` | The project to deploy (same convention as `./sb e2e`/`./sb ci`). |
| `--remote NAME` | Which registered, provisioned remote to deploy to. Required — no default remote is inferred, since a project could reasonably have zero or several. |
| `--json` | Print the result as JSON on stdout, for scripting/the MCP tool. |

**Exit codes**: `0` on a fully successful deploy; `1` on any failure (remote not
provisioned, unreachable, push rejected, diff-apply failure) — per spec FR-009, a
partial failure must never leave the VPS half-updated; a failed deploy is always safely
retryable by simply running `sb deploy` again.

**JSON output shape**:

```jsonc
{
  "ok": true,
  "remote": "myvps",
  "pushed_commit": "a1b2c3d",
  "uncommitted_files_applied": 4,
  "error": null
}
```

**Human-readable output** (no `--json`): a short confirmation naming the pushed commit
and how many uncommitted files were applied, e.g.:

```
Deploying to 'myvps'…
  pushed HEAD (a1b2c3d) -> myvps
  applied 3 modified + 1 untracked file(s)
Deployed. myvps now reflects your working tree as of this command.
```

## MCP surface

No new PER-CALL tool parameters are added to existing tools (`wp_cli`, `fs_read`,
`visit`, `run_tests`, etc.) — per this plan's key architectural decision, a remote
instance is reached by calling those SAME tool names against the SECOND, separately
registered MCP server (`sandbox-<remote-name>`), not by passing a `remote=` argument to
the existing `sandbox` server's tools. This keeps every existing tool's contract
byte-identical (spec FR-015).

The only genuinely new MCP-facing surface is local-side, mirroring the CLI:

```python
def remote_deploy(project_dir: str, remote: str) -> dict
```

Thin wrapper matching `run_tests`/`run_plugin_check`'s existing calling convention —
shells to `./sb deploy --project-dir <dir> --remote <name> --json` and parses the last
JSON line of stdout. Returns the same shape as the CLI's `--json` output above.

`./sb remote add/list/provision/up/down/remove` are NOT exposed as MCP tools in Phase 1 —
registering/provisioning a VPS is a deliberate, infrequent, credential-bearing action a
developer takes directly via the CLI, not something an agent should be able to trigger
autonomously by default. (Revisit only if a real workflow need for agent-driven
provisioning emerges.)

## `sandbox.config.json` — no new project-level keys

Unlike `pluginCheck`, this feature introduces NO new `sandbox.config.json` keys. Which
remote a deploy/instance targets is always an explicit `--remote <name>` argument (or the
MCP tool's `remote` parameter) — never a project-level default silently baked into
config. This is a deliberate asymmetry from `plugin-check`'s design: unlike "which plugin
to check" (near-always the project's own, a good default), "which remote to use" has no
single obviously-correct default even for one project (a developer might deploy the same
project to different VPSes for different reasons), so requiring an explicit choice every
time is the safer, clearer default here.
