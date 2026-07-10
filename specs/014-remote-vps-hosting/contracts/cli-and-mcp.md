# Contracts: CLI + MCP surface — remote VPS hosting

## `./sb remote` subcommand group

| Command | Purpose |
|---|---|
| `./sb remote add <name> ssh://user@host[:port]` | Register a VPS target (stored in `sandbox.local.yml`'s `remotes:` block). Idempotent — re-running for an existing name updates it. |
| `./sb remote list` | Show configured remotes, each with SSH reachability and provisioned status; SSH targets are write-only and never returned. |
| `./sb remote provision <name> --control-host <host>` | SSH in and run `scripts/install-remote.sh`: install Docker CE + compose plugin, Caddy, the `sb` runtime, provision the `visit` tools venv, start the loopback-bound remote MCP server behind public HTTPS, mint a bearer token. Safe to re-run (spec FR-005). |
| `./sb remote provision <name> --control tailscale` | Same provisioning flow, but install/join Tailscale and bind the remote MCP server to the Tailscale interface instead of using Caddy/HTTPS. |
| `./sb remote up <name>` / `./sb remote down <name>` | Start/stop the remote MCP server process on the VPS (over SSH) — does NOT affect running WordPress instances or the selected control transport itself. |
| `./sb remote remove <name>` | Forget the remote locally. MUST NOT touch anything on the VPS itself (spec FR-003) — existing instances there keep running; the user is told this explicitly in the confirmation message. |

Exit codes: `0` on success; `1` on any failure, with a human-readable `die()`-style
message naming the actual cause (unreachable host, SSH auth failure, install-step
failure) — never a bare stack trace.

Interactive `remote provision` asks whether the user wants Tailscale instead of the
default public HTTPS control endpoint. `--json` and non-interactive invocations default
to HTTPS and require `--control-host <host>` unless the host is already stored on that
remote.

## `./sb deploy` command

```
./sb deploy --project-dir DIR --remote NAME [--ensure] [--expose] [--domain HOST] [--plugin-slug SLUG] [--json]
```

| Flag | Purpose |
|---|---|
| `--project-dir DIR` | The project to deploy (same convention as `./sb e2e`/`./sb ci`). |
| `--remote NAME` | Which registered, provisioned remote to deploy to. Required — no default remote is inferred, since a project could reasonably have zero or several. |
| `--ensure` | After code deploy succeeds, run `sb ensure` on the VPS-side deploy target and return the remote instance metadata. |
| `--expose` | Implies the remote instance must be ensured; add/update a Caddy public HTTPS route, set WordPress `home`/`siteurl`, and return the public URL. |
| `--domain HOST` | Public hostname for `--expose`. If omitted, defaults to `default-<project-slug>.sandbox.asb.bd`. DNS must already point at the VPS. |
| `--plugin-slug SLUG` | Plugin slug to symlink into the remote instance and activate after `--ensure`. Defaults to the project config slug, then the deploy target slug. |
| `--json` | Print the result as JSON on stdout, for scripting/the MCP tool. |

**Exit codes**: `0` on a fully successful deploy; `1` on any failure (remote not
provisioned, unreachable, push rejected, diff-apply failure, instance boot failure,
plugin activation failure, route failure) — per spec FR-009, a partial failure must
never leave the VPS half-updated; a failed deploy is always safely retryable by simply
running `sb deploy` again.

**JSON output shape**:

```jsonc
{
  "ok": true,
  "remote": "myvps",
  "pushed_commit": "a1b2c3d",
  "uncommitted_files_applied": 4,
  "instance": {
    "instance": "my-plugin",
    "label": "default",
    "wordpress_port": 8188,
    "url": "https://default-my-plugin.sandbox.asb.bd",
    "admin_url": "https://default-my-plugin.sandbox.asb.bd/wp-admin/",
    "login_url": "https://default-my-plugin.sandbox.asb.bd/?sandbox_autologin=..."
  },
  "url": "https://default-my-plugin.sandbox.asb.bd",
  "error": null
}
```

When neither `--ensure` nor `--expose` is set, `instance` and `url` are `null`, and the
command behaves as a code-transfer-only deploy.

**Human-readable output** (no `--json`): a short confirmation naming the pushed commit
and how many uncommitted files were applied, e.g.:

```
Deploying to 'myvps'…
  pushed HEAD (a1b2c3d) -> myvps
  applied 3 modified + 1 untracked file(s)
  remote instance: my-plugin
  public URL: https://default-my-plugin.sandbox.asb.bd
Deployed. myvps now reflects your working tree as of this command.
```

## MCP surface

No new PER-CALL tool parameters are added to existing tools (`wp_cli`, `fs_read`,
`visit`, `run_tests`, etc.) — per this plan's key architectural decision, a remote
instance is reached by calling those SAME tool names against the SECOND, separately
registered MCP server (`sandbox-<remote-name>`), not by passing a `remote=` argument to
the existing `sandbox` server's tools. This keeps every existing tool's contract
byte-identical (spec FR-016).

The only genuinely new MCP-facing surface is local-side, mirroring the CLI:

```python
def remote_deploy(
    project_dir: str,
    remote: str,
    ensure: bool = True,
    expose: bool = True,
    domain: str | None = None,
    plugin_slug: str | None = None,
) -> dict
```

Thin wrapper matching `run_tests`/`run_plugin_check`'s existing calling convention —
shells to `./sb deploy --project-dir <dir> --remote <name> --json`, adds
`--ensure`/`--expose` by default, forwards `domain` and `plugin_slug` when provided, and
parses the last JSON line of stdout. Returns the same shape as the CLI's `--json` output
above, including `instance` and `url` for the one-shot public instance path.

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
