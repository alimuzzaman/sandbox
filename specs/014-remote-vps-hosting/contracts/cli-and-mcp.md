# Contracts: CLI + MCP surface — remote VPS hosting

## `./sb remote` subcommand group

| Command | Purpose |
|---|---|
| `./sb remote add <name> ssh://user@host[:port]` | Register a VPS target (stored in `sandbox.local.yml`'s `remotes:` block). Idempotent — re-running for an existing name updates it. |
| `./sb remote list` | Show configured remotes, each with SSH reachability, a safe diagnostic state/latency, and provisioned status; SSH targets are write-only and never returned. |
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
./sb deploy --project-dir DIR --remote NAME [--ensure] [--expose] [--domain HOST] [--plugin-slug SLUG] [--deploy-timeout SECONDS] [--json]
```

| Flag | Purpose |
|---|---|
| `--project-dir DIR` | The project to deploy (same convention as `./sb e2e`/`./sb ci`). |
| `--remote NAME` | Which registered, provisioned remote to deploy to. Required — no default remote is inferred, since a project could reasonably have zero or several. |
| `--ensure` | After code deploy succeeds, run `sb ensure` on the VPS-side deploy target and return the remote instance metadata. |
| `--expose` | Implies the remote instance must be ensured; add/update a Caddy public HTTPS route, set WordPress `home`/`siteurl`, and return the public URL. |
| `--domain HOST` | Public hostname for `--expose`. If omitted, defaults to `default-<project-slug>.sandbox.asb.bd`. DNS must already point at the VPS. |
| `--plugin-slug SLUG` | Plugin slug to symlink into the remote instance and activate after `--ensure`. Defaults to the project config slug, then the deploy target slug. |
| `--deploy-timeout SECONDS` | Bounded Git push budget for this deploy, from 1 to 3600 seconds; default 120. The remote job transport derives the same budget from its job deadline, with a 120-second minimum and 3600-second cap. |
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

## Convergence amendment — 2026-08-13: verification, source, and selection

### Authenticated verification

The local adapter exposes a read-only verification operation (CLI may present it
as `./sb remote verify NAME [--json]`; equivalent existing status surfaces may
delegate to the same service). It sends the stored credential through the
supported transport and returns a safe envelope:

```json
{
  "ok": true,
  "remote": "name",
  "authenticated": true,
  "endpoint": {"scheme": "https", "host": "safe-host"},
  "revision": "opaque-or-short-sha",
  "error": null
}
```

Tokens, SSH targets with credentials, Basic Auth userinfo, authorization
headers, and raw transport traces are forbidden in stdout, stderr, JSON, and
exceptions. Authentication failure is a bounded `remote_auth_failed` result;
missing capability is `auth_verification_unavailable`, not an invitation to
reveal or copy the credential.

### Immutable deploy source

`sb deploy` MUST accept `--source-ref REF` (full SHA or named ref). The adapter
resolves `REF` to a full immutable commit before the first remote mutation,
records both values, and rejects a missing ref or dirty-tree combination with a
nonzero `source_not_immutable` result. The existing working-tree deploy remains
available only through its explicit path and never silently falls back from a
failed immutable resolution.

When the working-tree source is a detached HEAD, the adapter MUST stage the
resolved committed base through a content-addressed `sandbox-source-<sha>` ref
and may then apply the current uncommitted overlay. It MUST NOT create, switch,
or force-push a user branch; the result identifies this source path as
`source_mode: "detached"`.

### Nested manifest root

Manifest resolution returns both `manifest_path` and `source_root`. Compose,
file transfer, generated runtime paths, and result evidence use `source_root`
derived from the validated manifest location; all paths must remain within the
canonical project root. A nested manifest cannot be parsed successfully and
then deployed from an unrelated checkout parent.

### Selection precedence and surfaced result

For operations that permit target inference, precedence is: explicit
`--remote`, an operation/profile-resolved target, then a single eligible
configured target. Zero or multiple eligible targets fail rather than silently
switching machines. `deploy` continues to require explicit `--remote`. Every
human and JSON result includes the selected remote name and source of selection
(`explicit`, `profile`, or `single-configured`) without secret fields.
## Hosting deployment convergence evidence

`host apply`, `host status`, and `host diagnose` retain four distinct revision facts:
the requested source, staged/pending receipt, locally recorded successful revision, and
observed runtime revision. Missing observation never collapses these into one nullable
`deployed_revision`. The legacy field remains for compatibility.

Runtime convergence and public-edge observation are separate phases. Exact runtime
revision for every declared service/key plus the full declared healthy topology may
reconcile a stale local record without a Compose mutation when the saved configuration
digest also matches. An `edge: pending` replay with that evidence is edge-only; missing
evidence refuses without Compose or initializer replay. A changed source always takes the
full recreate path. Targeted convergence also requires exact identity/config plus ready
topology and health. Dirty-allowed source identity includes a bounded digest of the
single immutable artifact and deletion set whose exact bytes are transferred. Since that
digest is not observable inside the runtime, dirty receipts cannot select commit-only
reconciliation or edge-only replay.
Source receipts declare identity version 2. Legacy unknown same-revision receipts and an
unchanged known v2 dirty artifact refuse before target reset regardless of runtime/edge
phase; a different known v2 dirty artifact takes full convergence. Only the exact
unversioned v1 empty-overlay digest may migrate to clean evidence. Hosting uses a 4,096-file/64 MiB
artifact envelope. Public deploy retains its separately admitted 10,000-file/512 MiB
include envelope and validates it before remote admission or mutation.
A staged unverified retry reconciles from observation or refuses without rerunning an
initializer. Unknown service health is `unverified`, not ready. Remote
Compose/source observation uses one bounded session with a strict shared deadline,
allowlisted revision keys, process-level bounded output draining, and bounded fan-out.
It retains phase-level partial evidence. Partial evidence is persisted before a failed
apply returns.
Persisted/status source checks contain only match, missing, or mismatch state and never a
raw nonmatching environment value. Exact reconciliation retains the full bounded
classified service/topology/health/source/phase receipt. Source-root Git boundary lookup
scrubs inherited repository selectors.
