# Remote VPS hosting for sandbox instances

## Agent-aware source sync

Use the opt-in one-time path to transfer one credential-screened generation
without running `host apply` or restarting Compose:

```bash
./sb host sync --project-dir /path/to/site --environment production \
  --remote myvps --request-id edit-20260828-01 --json
```

For a bounded caller-owned watch loop, add `--watch --watch-seconds 3600`.
Capture is Git-relative and refuses the complete generation when tracked,
modified, untracked, or explicitly included input looks credential-like. The
remote replaces only sync-owned files atomically; it preserves Git metadata,
runtime state, and unknown files. The result reports `restarted: false`.

This is not promotion. A later `host apply --confirm` restores the committed
revision and performs the controlled Compose, route, and health workflow.

Disposable development workspaces also expose the relationship-owned `sync`
surface. It is off by default and always requires an explicit registered remote
and durable workspace ID:

```bash
./sb sync start --mode checkpoint --project-dir /path/to/project \
  --remote myvps --workspace-id ws_opaque --participant-id session-a --json
./sb sync once --checkpoint --project-dir /path/to/project \
  --remote myvps --workspace-id ws_opaque --request-id checkpoint-1 --json
./sb sync stop --project-dir /path/to/project \
  --remote myvps --workspace-id ws_opaque --json
```

`live` accepts non-blocking commit/event signals; `checkpoint` transfers only
on an explicit checkpoint; `off` never starts an automatic transfer. Stop keeps
accepted and pending generation state visible. It does not cancel jobs, delete a
workspace, revert source, or grant reset/takeover authority. An explicit apply
may reset synchronized uncommitted source to the committed revision.

Participants that resolve to the same durable project identity share one
ordered relationship. Symlink locators may share that identity. A fresh clone
or unresolved relocation is a different owner and is refused before source
transfer until the existing lifecycle adoption flow explicitly preserves the
durable identity. Ownership errors expose opaque IDs only, never checkout paths.

Lost acknowledgments reconcile with the original request identity. Remote
divergence is never adopted or overwritten automatically; `sync resolve
--resolution keep-local --confirm` clears the conflict gate and leaves sync off
so the next explicit request repeats normal ownership checks.

## 1. What this is

`./sb remote` + `./sb deploy` let you run a sandbox instance on a VPS you already own
and manage, instead of on your local machine — reached over a public HTTPS control
endpoint by default, with Tailscale available as an explicit opt-in, and with the exact
same CLI/MCP surface as a local instance. This is a first-class capability
(`specs/014-remote-vps-hosting/`), grounded in a deeper feasibility study at
`docs/remote-hosting-prd.md` (read that doc's §0 for the resolved architecture
decisions this feature encodes).

**Not this**: a continuous file-sync daemon, an on-demand-power-managed VPS, or a
multi-tenant/shared-VPS story. See §5 for what's explicitly out of scope.

## Choose the remote workflow

Sandbox has four related remote capabilities with deliberately different purposes.
They may use the same VPS, but one is not an implicit substitute for another.

| Need | Use | What it does | Boundary |
|---|---|---|---|
| Put the current checkout on a VPS for an instance or preview | `./sb deploy --remote NAME` | Makes a one-way snapshot of the local source tree; `--ensure --expose` can boot and expose that sandbox instance. | It is not continuous sync or a production promotion. |
| Run a test, E2E run, matrix, or compatible CI remotely | `./sb test` / `exec` / `ci run --remote NAME` | Deploys the selected source revision to a named development workspace, then returns a durable job ID. | It is development execution, not a public-hosting command. |
| Inspect or control those remote jobs from an agent | The co-located `sandbox-NAME` MCP server | Reads durable job status/output and calls the same VPS-side operations without keeping SSH pipes open. | It is a control plane, not a source-sync or deployment mechanism. |
| Operate a declared public service | `./sb host plan` then `./sb host apply --confirm` | Applies a checked hosting manifest, health checks, Caddy/DNS policy, and declared secrets for an environment. | It is separately confirmation-gated and never inferred from a development deploy or job. |

Use `./sb host status --project-dir DIR --environment ENV --remote NAME --json`
for a read-only deployment snapshot. It reports the recorded deployed revision,
the configured service names, and bounded Compose state/health; if the remote
cannot be observed, the response keeps the revision metadata and returns an
explicit `health.state=unavailable` reason without mutating the host.

`./sb deploy` is project-scoped. The checkout selected by `--project-dir` is
the source of truth for the remote deploy repository and the instance it
ensures. The global `--instance` selector is rejected for this command rather
than silently ignored; use the intended project directory explicitly.

The remote-home preflight and Git push budget both default to 120 seconds. The
home is resolved once before any deploy-target mutation. For a large first-time transfer,
set a bounded value explicitly with `--deploy-timeout SECONDS` (1-3600). Remote
test/job submissions derive the same budget from their job deadline, retaining
a 120-second minimum and a 3600-second cap. A push timeout is reported as a
handled, command-free error; inspect the remote deployment state before replaying
because the final state is unknown.

Runtime source uploads used by confirmed `remote provision`, `remote up`, and
`remote service migrate` default to 300 seconds. Set their SSH upload budget
with `--upload-timeout SECONDS` (1-7200); the local package budget remains fixed
at 300 seconds. An upload timeout has unknown completion and is never retried
automatically.

If the managed remote branch has moved independently, deploy fails with the stable
`remote_branch_diverged` error code. Sandbox never force-pushes that branch. Inspect
the remote branch and explicitly reconcile it with the intended local source before
retrying; the refusal is reported separately from an SSH or unknown Git failure so a
caller does not blindly replay a conflicting deployment.

Each deploy resets the remote checkout to the pushed revision and removes only
untracked, non-ignored files before applying the current uncommitted layer. The
reset is supervised on the remote for 120 seconds with a 15-second termination
grace; the local SSH client allows that grace plus connection overhead. A reset
timeout leaves completion ambiguous, so inspect the remote deployment state
before replaying the same request.

Before remote workspace staging or durable test/job submission, Sandbox performs
a read-only Docker network-capacity admission at the shared exact-tree staging
seam. The probe must
observe the configured address pools and every user-defined network's IPAM
subnet, then report usable subnet capacity. Foreign and unattributed networks
reduce usable capacity just like Sandbox-owned networks; a raw network count or
filesystem free-space value is not evidence. Missing or partial probe data, and
pool exhaustion, allocation collisions, or ambiguous inventory fail before the
source tree is staged. The admission makes one bounded probe call; it does not
retry or delete networks automatically. A blocked admission can recover only
after a fresh complete probe proves usable capacity. The bounded refusal points
to the reviewed plan workflow (`./sb remote docker-pool NAME --json`);
operators must not remove Docker networks directly or treat disk capacity as a
network-capacity fix.

The Docker-pool transaction also treats a client-side timeout as an unknown
outcome. The safe error omits the generated transaction command (including its
encoded program); inspect the remote receipt and running-container state before
considering any replay.

The Docker-pool plan reports measured capacity for the desired address pools:
`subnet_capacity_total`, `subnet_capacity_allocated`, and
`subnet_capacity` (usable). The measurement inspects user-defined network IPAM
with full IDs. If network inventory, IPAM, or allocation accounting is incomplete,
`subnet_capacity_status` is `partial` or `unavailable` and usable capacity is
`null`; the plan never substitutes the historical fixed pool total or a raw
network count. Treat that result as an evidence gap and keep the operation
fail-closed until a fresh complete probe is available.

At the transport boundary, a blocked admission is normalized to one bounded
error envelope. It always reports `ok: false`, `status: blocked`, one of the
stable capacity codes (`docker_network_capacity_unavailable`,
`docker_network_subnet_exhausted`, or `network_allocation_conflict`), and
`staging_started: false` / `network_allocation_started: false`; an unknown or
malformed code fails closed to `docker_network_capacity_unavailable`. Only the
validated remote name is exposed as target metadata. Capacity, evidence, and
recovery are reduced to their documented safe summaries; probe output, paths,
subnets, commands, SSH diagnostics, and exception details are not forwarded.
The CLI emits that envelope once with `--json` and exits 1, or prints the fixed
safe recovery line for human use and exits 1. Remote job and E2E submission
adapters preserve the same envelope, while `run_tests` additionally keeps its
`passed: false`, `summary: null`, `output: ""`, and resolved `mode` fields; no
job is accepted and no job ID is returned.

For command examples and operational boundaries, see
[`docs/remote-hosting-implementation.md`](remote-hosting-implementation.md). Durable
job recovery and retention are documented separately in
[`docs/remote-job-runtime.md`](remote-job-runtime.md).

## 2. The model in one sentence

**Co-location, not remote-control.** The MCP server, `sb`, `$SANDBOX_HOME`, Docker, and
all containers move onto the VPS together — you reach them through a SECOND, separately
registered MCP server (`sandbox-<remote-name>`), not by adding a `--remote` flag to your
existing local tools. By default that second MCP server is exposed as
`https://<control-host>` through Caddy, while the MCP process itself stays bound to
`127.0.0.1`. Your local `sandbox` MCP server and all your local instances are completely
unaffected.

## 3. First-time setup

```bash
./sb remote add myvps ssh://ubuntu@203.0.113.10
./sb remote provision myvps --control-host sandbox-control.example.com --confirm
```

Optionally annotate a remote with its provider in the machine-local, gitignored
`sandbox.local.yml` file:

```yaml
remotes:
  myvps:
    provider: hetzner
```

`provider` is an optional lowercase slug. `./sb remote list` shows that value, or
`unknown` when it is not set. It is descriptive metadata only: Sandbox never uses it to
infer transport, behavior, or billing. Edit it directly in the machine-local config when
the annotation changes.

Remote-list JSON also includes a safe `reachability` state (`reachable`, `timeout`,
`authentication_failed`, `dns_failed`, `connection_refused`, `network_unreachable`,
`unreachable`, or `probe_unavailable`) and bounded probe latency. The state is
diagnostic metadata only; SSH targets and probe output are never returned.

`provision` asks whether you want Tailscale instead of public HTTPS when run
interactively. In `--json`/non-interactive mode it defaults to HTTPS; pass
`--control tailscale` to opt into Tailscale explicitly. It is plan-first: omit
`--confirm` to inspect the selected transport without modifying the VPS.

Every confirmed provision creates an owner-only, secret-redacted journal under
`$SANDBOX_HOME/runtime/remote-provision/<name>/`. Its opaque ID is included in
the final JSON receipt. If the caller is interrupted before that receipt, the
next plan reports the last journal's ID and `in_progress` state, so operators
can inspect the local evidence before deciding whether a new provision attempt
is safe. Journals record milestones only; they never retain SSH targets,
bearer tokens, or raw remote output.

For HTTPS mode, `provision` SSHes in and, non-interactively, installs Docker CE +
compose plugin, Caddy, the `sb` runtime itself, the MCP server venv, and the `visit`
tools venv (Playwright + headless Chromium — needed server-side, since `visit` must
reach `localhost:<port>` and the VPS's own `.tst` proxy). It stages the current local
sandbox checkout onto the VPS rather than assuming the sandbox GitHub repo is
anonymously cloneable. It then starts the remote MCP server (streamable-HTTP, bound to
`127.0.0.1`, never `0.0.0.0`) and configures a Caddy virtual host that proxies
`https://<control-host>` to it.

For Tailscale mode, `provision` also installs Tailscale (joining the tailnet if
`TAILSCALE_AUTHKEY` is set in your shell environment before running provision —
otherwise it installs the package and you `tailscale up` on the VPS manually once, then
re-run provision). The remote MCP server binds to the VPS's Tailscale interface instead
of loopback.

The SSH user must be able to run `sudo` non-interactively for package installs. On a
fresh VPS, either provision as a sudo-capable user with NOPASSWD configured, or do a
one-time root bootstrap to grant that user package-install rights. HTTPS mode also needs
the `--control-host` DNS name to resolve to the VPS; Tailscale mode needs either
`TAILSCALE_AUTHKEY` or a one-time manual `tailscale up`.

Sandbox opportunistically reuses one authenticated OpenSSH connection for repeated
SSH and SCP calls to the same endpoint. The control socket lives under the local
Sandbox runtime with owner-only permissions, uses OpenSSH's endpoint hash rather
than a readable host/user name, and expires after 600 idle seconds. Commands remain
independent: each keeps its own timeout, exit status, output handling, and
confirmation gate. If local multiplexing state cannot be prepared, Sandbox makes
one ordinary SSH connection; it never replays a command after launch.
The shared process runner also rejects shell-like string commands, NUL-bearing arguments or
environment values, and invalid timeout bounds before a subprocess is launched.
Runtime health probes accept only HTTP(S) URLs and finite non-negative timeouts; unsupported
schemes and malformed probe inputs fail closed.

Runtime uploads and dirty-file deployment use one streamed archive/session where
possible. This avoids one SSH channel for every `mkdir` and `scp`; the control
master still provides safe opportunistic fallback when a socket disappears or
the host is unavailable. Long-lived MCP HTTP access uses the remote server's
HTTPS/Tailscale transport rather than an SSH tunnel; a tunnel would add
forwarding lifecycle without reducing ordinary command-shell latency.

Both archive paths omit macOS AppleDouble sidecars whose basename starts with
`._` (at any directory depth). The filter is deliberately basename-only:
ordinary dotfiles such as `.env` and `.gitignore` remain eligible, and transferred
files keep their local bytes. When sidecars are encountered, Sandbox reports only
a bounded count of skipped entries; it never prints their paths or contents.

Register the second MCP server through your client’s supported secret mechanism. The
remote bearer credential is never printed, returned in JSON, embedded in a command,
or copied into a service definition. It stays in Sandbox's owner-only local secret
store and is transferred to the remote only on standard input while the remote service
credential file is created. Your local `sandbox` MCP server registration is completely
untouched.

## 4. Deploying code

```bash
cd ~/some/plugin/project
./sb deploy --remote myvps --deploy-timeout 600
```

This is a **one-way, on-demand** push — never a continuous sync. Every deploy:

1. `git push`es your current `HEAD` to a deploy-target git repo on the VPS (works even
   for a branch never pushed to GitHub/origin — it's a direct git-to-git push over your
existing SSH connection).
2. Resets the VPS's working tree to that commit.
3. Applies your CURRENT uncommitted changes on top — both edits to tracked files and
   brand-new untracked files (excluding only `._*` AppleDouble sidecars by basename).
   This step REPLACES whatever a previous deploy applied; it never stacks. "Is my code
   live on the VPS" always has one answer: "as of my last `./sb deploy`."
4. Transfers the selected primary project descriptor (`sandbox.config.*` or
   `.wp-env.json`) even when the checkout keeps that file out of Git, so the remote can
   reproduce plugin mounts. Machine-only `sandbox.config.override.*` and secret files
   are never included by this exception.

Gitignored build artifacts are excluded by default. For a reviewed, bounded exception,
repeat `--include PATH` with a relative file or directory such as a generated Composer
`vendor/` tree:

```bash
./sb deploy --project-dir /path/to/plugin --remote myvps --ensure \
  --include vendor/
```

Sandbox expands the directory to regular files, rejects machine-local or secret-looking
paths (`.env*`, private-key files, `.git`, and `.sandbox`), and caps the transfer at
10,000 files/512 MiB. The JSON result reports the exact included relative files under
`included_paths`; the option is explicit and is never inferred from `.gitignore`.

Before the remote instance is considered ready, `ensure` reconciles each instance's
published WordPress, database, and Mailpit ports against listeners already present on
the host. If a stale container or unrelated process owns a recorded port, Sandbox moves
the instance to a free trio and regenerates Compose before booting. This prevents a
partial `compose up` from leaving WordPress reachable while its WP-CLI database path is
broken.

There is no project-level config for this — you always pass `--remote <name>`
explicitly (unlike `plugin-check`'s slug, there's no single obviously-correct default
remote for a project, so no default is guessed).

### One-shot deploy + public instance

For agent workflows, use the one-shot form. It deploys the current working tree, boots or
refreshes the remote WordPress instance, activates the deployed plugin slug, configures a
public HTTPS hostname through Caddy, updates WordPress `home`/`siteurl`, and returns the
URL in the JSON result:

```bash
./sb deploy \
  --project-dir /path/to/plugin \
  --remote myvps \
  --ensure \
  --expose \
  --domain default-templately-ai-builder.sandbox.asb.bd \
  --plugin-slug templately-ai-builder \
  --json
```

If `--domain` is omitted, sandbox uses `default-<project-slug>.sandbox.asb.bd`.
DNS for that hostname must already point at the VPS. In MCP, `remote_deploy(...)`
defaults to `ensure=true` and `expose=true`, so agents get the full remote instance and
public URL path unless they explicitly opt out.

Remote `ensure` runs one bounded, non-multiplexed SSH liveness probe before source
deployment. If the probe fails, it returns `remote_unreachable` with the safe state
and `--local` recovery hint; no Git push, workspace staging, or remote instance
mutation starts.

When `--ensure` or `--expose` is requested, deploy first records the selected remote's
read-only instance inventory. If remote `ensure` fails before returning a usable instance,
Sandbox compares the post-failure inventory with that baseline and removes only one
uniquely new default instance. An unavailable or ambiguous inventory fails closed and
leaves the remote unchanged or reports cleanup as unverified; Sandbox never guesses which
pre-existing instance to delete.

Host apply keeps bounded diagnostics at the SSH boundary. If a remote command times
out, the CLI reports the timeout and a redacted tail of any partial output instead of
discarding the captured stream; the remote command is not replayed automatically
because its final state is unknown. During the 60-second loopback health window,
Sandbox also emits a progress line every ten seconds with the last safe probe result.
Use the host logs command for the declared service logs after a deployment:

```bash
./sb host logs --remote myvps --project-dir /path/to/site --environment production
```

These diagnostics are bounded and are not a substitute for live-host acceptance or
an unbounded log-follow mode.

#### Extra hostnames (`--alias`)

`--alias HOSTNAME` (repeatable) exposes the instance on additional hostnames
pointing at the same port — a CDN pull-zone origin, for example. It defaults to
the project's `sandbox.config.json` `aliases`, so declared aliases travel with
the project and do not have to be repeated on the command line. Aliases also
reach the instance's `WP_HOME`/`WP_SITEURL` so WordPress serves them as
themselves instead of redirecting to the primary domain; see **Aliases** in
`docs/sandbox-config-reference.md` for what that does and does not trust.

The primary `--domain` is routed first, so a bad alias never leaves the
instance unreachable on its own hostname. DNS for every hostname must already
point at the VPS.

Routes are per-hostname files on the remote, so changing `--domain` between
deploys leaves the old route serving. Deploy reports those as `stale_routes` in
its JSON and in the human output. `--prune-routes` deletes the ones that proxy
to this instance's port and are neither the current domain nor a declared
alias. It is opt-in: the inventory is read from the whole host, so a route may
belong to a checkout whose config this project cannot see.

### Generic Compose projects

The same command works for an explicit non-WordPress Compose project. Its
`sandbox.config.json` must declare `kind: "compose"`, the public Compose service,
the service's internal port, and a health path. `deploy --ensure` transfers the
working tree and runs the normal generic lifecycle on the remote; `--expose` routes
the resulting generic HTTP port through Caddy and returns the public URL. No plugin
activation or WordPress URL update is performed. `--plugin-slug` is WordPress-only and
is rejected for generic projects so an accidental flag cannot be silently ignored.

### Pro plugins on the remote host

Pro plugins are not project code — they are a machine-level catalog. Locally they
live in one store directory (`defaults.pro_plugins_home`, default
`~/Sites/plugins-pro`) and are registered slug -> path in the user-global catalog,
which is what makes every local instance list them on **Plugins -> Sandbox
On-Demand** without installing them.

`./sb deploy` mirrors that whole store to `<remote $SANDBOX_HOME>/plugins-pro` and
merges its slugs into the REMOTE user-global catalog, so **every** instance on that
host offers the same on-demand plugins. Run it on its own with:

```bash
./sb remote plugins myvps            # mirror now
./sb remote plugins myvps --dry-run  # what would go up, transfers nothing
./sb remote plugins myvps --force    # re-push even when nothing changed
./sb deploy --remote myvps --no-pro-plugins   # skip the mirror for this deploy
```

Behavior:

- Only directories carrying a WordPress `Plugin Name:` header are advertised; loose
  files and zips still ride along in the mirror, but never enter the catalog.
- The mirror is `rsync --archive --delete`: the remote store is a copy of the local
  store as of the last push, never a continuous sync. `.git/`, `node_modules/`,
  `.DS_Store`, `.idea/`, `.vscode/` are excluded.
- A content fingerprint is recorded per remote under
  `$SANDBOX_HOME/runtime/pro-plugins/<remote>.json`, so an unchanged re-push is a
  no-op and deploying stays fast.
- Catalog entries are written as bare paths, which resolve to **on-demand**: present
  on the On-Demand page, never auto-activated. A slug the host configured itself
  (an object entry, or a path outside the store) is reported as a conflict and left
  untouched. A slug removed from the local store is unregistered on the next push.
- Mirroring is fail-soft inside `deploy`: the project deploy still succeeds and the
  JSON result carries `pro_plugins.ok=false` with the reason.
- Licensing is unchanged — mirroring ships the code, not the keys (`./sb license`).

## 5. Using a remote instance

Once deployed, boot and use it exactly like a local project — but by running commands
directly on the VPS (SSH in, or via the second MCP server), not via a `--remote` flag on
your local `sb`/MCP tools:

```bash
ssh ubuntu@203.0.113.10 "cd \$SANDBOX_HOME/deploy-src/<project-slug> && ./sb ensure --project-dir ."
```

## Managed Compose hosts

Any Docker Compose project can carry a project-local `sandbox.hosting.yml`. It describes
a Compose web service, optional long-lived worker services, deployment policy,
primary hostname, aliases, redirects, and the required Cloudflare policy.

The `--environment` option may be omitted only when the manifest declares exactly one
environment. Manifests with multiple environments require an explicit declared name;
Sandbox does not infer a default environment. If a selection is missing or unknown,
validation lists a bounded, escaped set of the declared names so the choice can be
corrected without exposing manifest text as terminal control sequences.

For a read-only inventory of a multi-environment manifest, use the explicit
`--all` mode with `validate`:

```bash
./sb host validate --project-dir /path/to/site --all --json
```

The result contains one bounded validation document per declared environment and a
top-level `ok` value. It exits non-zero if any environment is invalid. `--all` cannot
be combined with `--environment` and is rejected for mutating or remote actions.

```bash
./sb host validate --project-dir /path/to/site
./sb host plan --project-dir /path/to/site --environment production --remote myvps
./sb host apply --project-dir /path/to/site --environment production --remote myvps --confirm
./sb host logs --project-dir /path/to/site --environment production --remote myvps --lines 200
```

`validate` is offline. `plan` is read-only and lists only the declared hostnames;
it never prunes unrelated DNS records. Before `apply` contacts the remote, Sandbox
checks the local Git branch and clean-tree policy declared for the target environment.
`apply` is confirmation-gated: it then transfers the approved checkout, runs
Compose/init health checks, converges Caddy, and updates only declared DNS records.
Sandbox records the requested source, staged source receipt, recorded revision, and
observed runtime revision separately. The staged receipt is saved before runtime
observation, and exact runtime/topology evidence is saved before edge verification.
An observation timeout therefore leaves explicit `runtime: unverified` evidence with a
bounded unavailable phase; an edge timeout leaves `runtime: ready` with `edge: pending`
instead of erasing the successful runtime convergence. A bounded partial observation is
also persisted as `runtime: unverified` with its completed/timeout phases before apply
fails. A later apply repairs only the
local record when every declared long-lived service and every declared revision key
exactly matches the requested revision, the full topology is healthy, and the saved
configuration digest matches. It does not recreate containers merely to repair that
record. Dirty-allowed deployments snapshot one bounded immutable tar artifact, hash that
artifact plus its deletion set, transfer those exact bytes, and persist only the digest.
Because that digest is not runtime-observable, dirty receipts never use commit-only
record reconciliation or edge-only replay. A clean staged `unverified` receipt is
observed and reconciled or refused; a dirty one is refused unless a real source change
requires full convergence. Missing observation alone never reruns Compose initializers.
Source receipts persist `source_state_identity_version: 2`. At the same revision/config,
legacy missing/unknown identity evidence and an unchanged known v2 dirty artifact refuse
before target reset regardless of runtime/edge phase. A different known v2 dirty artifact
is a real source change and takes full convergence. Only the historical unversioned v1
empty-overlay digest is migrated as proven clean; changing a manifest from dirty-allowed
to clean does not rewrite other prior evidence.
Hosting bounds its source artifact to 4,096 files and 64 MiB. This is intentionally
narrower than public `sb deploy --include`, whose existing 10,000-file/512 MiB admission
contract is validated before any remote admission or mutation.
The Caddy fragment transaction holds one host-global lock, compares the desired and
installed fragment digests, and skips validation/reload when both the fragment and
aggregate import are unchanged. A real change runs separate 30-second validation,
reload, and active-service observation phases. Their sanitized phase/digest receipts
are appended to the mode-0600 apply log. Rollback restores the exact previous fragment
(or its previous absence) and records `rollback_complete`; any failed restore is
reported as `rollback_incomplete` rather than being presented as recovery.
`logs` reads a bounded snapshot from the hosted web service and declared background
services; it does not hold an SSH stream open. If a declared service is absent from
the deployed Compose configuration, the output includes a bounded `[missing service:
NAME]` diagnostic and still returns logs for the services that are present.
Configure Cloudflare with `./sb connect cloudflare`, which
stores the token in `~/.zshrc.secrets` (owner-only and outside Git), and record the VPS
public address with `./sb remote set-origin myvps --ipv4 <address>`.

Permanent projects may declare public values plus required/generated secret mappings in
`sandbox.hosting.yml`. `./sb host secrets --project-dir /path --environment production`
reports names only; `--generate` creates declared generated values and `--set KEY`
stores a required value through a hidden prompt.

A clean-tree deployment may derive non-secret runtime values from the exact source
selected by `host apply`:

```yaml
deploy:
  allowed_branches: [main]
  require_clean: true
  # Host apply refuses to start below this free-space floor (MiB). A bounded
  # rollback reserve is held separately while Compose runs.
  min_free_disk_mb: 1024
  derived_environment:
    APP_SOURCE_REVISION: pushed_commit_sha
```

`pushed_commit_sha` is the only provider. Sandbox resolves the full lowercase 40-hex
source SHA before transfer, pushes that literal commit, and returns the same value,
including for nested-source deployments; it never trusts a later `HEAD` read, caller
environment variables, or a secret. A clean-tree deployment checks the source again
after the push and aborts before reset or Compose if a local overlay appeared in the
meantime. Local overlays are transferred only when `require_clean` is explicitly false.
The mapping key cannot overlap `secrets.values`, `secrets.required`, or
`secrets.generated`. Plan output reports only the key, provider, and
`resolved_at_apply: true`. Successful apply evidence reports the selected commit and
the same mapping metadata, never the rendered environment or secret values. An invalid
push result fails before the remote checkout is reset or Compose is started.

Use `compose.background_services` for declared long-lived workers that must be built,
recreated, and started with the web service. Keep one-shot migration/setup jobs in
`compose.init_services`; each service name must be unique across the three fields.

`compose.build` (default `true`) controls whether apply rebuilds images. Set it to
`false` for an environment whose image build does not fit the 900s deploy timeout: apply
then deploys config, secrets, and routing onto the image the remote already has, and
skips the explicit `init_services` build. Compose still builds a service that has no
image at all, so a first deploy works either way, and new application code only ships
once the image is rebuilt.
Targeted/no-build Compose convergence is guarded by exact source identity, configuration
digest, topology, and health proof, and never runs migration/setup initializers. Host apply uses
an even narrower replay rule: an exact same-source `edge: pending` receipt resumes only
edge work with zero Compose/initializer calls. Missing or partial replay evidence refuses
instead of mutating runtime. A changed source always uses the full recreate path even
when a saved configuration digest happens to match. Unknown Compose health is reported as
`unverified`, never `ready`.

For a deliberate cold or large build, set `compose.build_timeout_seconds` to a bounded
value from 60 through 7200 (default `900`). The value applies to the Compose `up` and
explicit `init_services` build operations; the final no-build restart and health probes
retain their own shorter limits. This keeps long builds observable and bounded instead
of silently using a fixed 15-minute cutoff.

Text-mode `host apply` reports source, Compose, initializer, and healthcheck progress as
the remote command runs. Compose output is streamed without changing the machine-readable
JSON contract and is appended to a mode-0600 remote log at the path returned as
`apply_log` in apply evidence. Read its bounded tail later with
`./sb host logs --remote NAME --apply-log --lines 1000`; timeout errors retain the
latest output tail rather than reducing a failed build to a bare timeout message.

For a one-command, read-only failure explanation use
`./sb host diagnose --remote NAME --json`. It combines the recorded deployed revision,
manifest-declared services, profile-aware configured Compose services, running service
rows, per-service Compose state/health, free disk, image metadata, derived source-revision
checks for every declared long-lived service, and the protected apply-log path. A
single bounded remote observer collects configured services, runtime rows, and all
declared source-revision keys under one strict total deadline. It queries only those exact
allowlisted keys, never a container's full environment, and bounds service/key fan-out,
rows, bytes, phases, and receipt size. Each subprocess pipe is drained incrementally into
a fixed-size buffer, so an untrusted command cannot allocate unbounded captured output
before truncation. Completed phases and partial or unknown evidence
survive a later probe failure; diagnose does not open one SSH session per service/key. A
revision mismatch is retained only as `mismatch`; the arbitrary container value is never
saved or returned by status. Exact record reconciliation preserves the bounded service,
topology, health, source-key-state, and phase receipt rather than collapsing it to ready.
Git checkout-boundary probes also discard inherited `GIT_*` repository selectors. A
declared service missing from either Compose configuration or the running set is
topology drift and makes readiness `degraded`. Init jobs and undeclared dependency
services are excluded from this long-lived topology comparison. Missing remote evidence
is reported as
`unavailable` or `degraded`; the command never mutates the host or prints secrets.

An environment may also protect its public origin with Basic Auth:

```yaml
basic_auth:
  username: operator
  password_secret: MY_SITE_BASIC_AUTH_PASSWORD
  bypass_ips:
    - 203.0.113.42
  bypass_paths:
    - /healthz
  bypass_routes:
    - path: /api/oauth2/token
      methods: [POST]
    - path_template: /api/v1/orgs/{orgId}/projects
      methods: [GET, POST]
```

## Keeping a public route out of search results

An environment may declare `robots: deny` beside `cloudflare:`:

```yaml
robots: deny   # default: allow
```

Caddy then answers `/robots.txt` with `User-agent: * / Disallow: /` for every served
hostname of that environment, ahead of the proxy (the proxy moves inside its own
`handle` so the two are mutually exclusive routes). The default `allow` leaves the
application's own `robots.txt` in charge — permanent hosting fronts real production
sites that want to be indexed.

Ephemeral routes are the other way round: `sb preview create` and the remote control
proxy deny by default (`_remote._caddy_proxy_command`), because a preview hostname is a
real public DNS record usually handed out with an autologin token in the URL. Fresh WP
instances also install with `blog_public=0`, which emits `noindex,nofollow` on every
page — the part that actually prevents indexing, since a `robots.txt` disallow alone
still permits URL-only listings from inbound links.

**Cloudflare can override all of this.** A zone with Cloudflare's managed
`robots.txt` / content-signals transform enabled has its own `User-agent: *` group with
`Allow: /` prepended to whatever the origin returns; on an equal-length path match the
least restrictive rule wins, so the managed `Allow: /` beats the origin's `Disallow: /`.
Verify what the edge actually serves (`curl https://<host>/robots.txt`), not just the
origin (`curl --resolve <host>:443:<origin-ip> …`). Turning the managed transform off is
a dashboard/API change in the Rulesets scope — a DNS/SSL-scoped API token cannot do it.

Set the referenced secret with `./sb host secrets --set MY_SITE_BASIC_AUTH_PASSWORD`.
On confirmed apply, Sandbox hashes the password on the remote and renders Caddy's
`basicauth` directive. Passwords and hashes are never committed, printed, passed in
argv, or included in the Compose environment. The gate is disabled when the block is
absent. Planning deliberately redacts the generated Caddy verifier; confirmed applies
fail rather than silently omit a declared gate.

`bypass_ips` is optional and accepts public IPv4 or IPv6 addresses. Any matching
client bypasses Basic Auth only when the request arrives through a Cloudflare proxy
and its `CF-Connecting-IP` header exactly matches a declared address. Direct requests
cannot spoof this bypass because Caddy also verifies the proxy source address against
Cloudflare's published ranges.

`bypass_paths` is optional and allows unauthenticated `GET` requests to exact,
non-root paths. It is intended for public discovery or health endpoints while the
rest of the hosted application remains behind Basic Auth.

`bypass_routes` is optional and permits only the declared HTTP methods on an
exact `path` or a segment-based `path_template`. A template must contain at
least one whole-segment `{parameter}`; Sandbox converts parameters to
single-segment Caddy matchers and does not accept raw regular expressions,
wildcards, root paths, or non-standard methods. Use this for machine protocols
whose application authentication must receive `POST` or Bearer-token requests
while the surrounding site remains behind Basic Auth. The application remains
responsible for authentication, authorization, rate limiting, and tenant
isolation on every bypassed route.

### One-time hosted WordPress login URLs

A WordPress hosting environment can opt in to a short-lived admin login link by
declaring its target MU-plugin path and login user in `sandbox.hosting.yml`:

```yaml
autologin:
  user: admin
  container_path: /var/www/html/wp-content/mu-plugins/99-sandbox-host-autologin.php
  ttl_seconds: 900
```

After a successful deployment, issue a link explicitly:

```bash
./sb host login-url --project-dir /path/to/wordpress --environment production \
  --remote myvps --confirm
```

The returned `?sandbox_autologin=` URL expires after the requested lifetime (or the
manifest default) and can set an admin session once. Sandbox stores only a SHA-256
hash in the running container, never records the token in Git or host state, and a
new link replaces the previous unused link. Treat the returned URL like a password.

The default policy is Cloudflare-proxied DNS with Origin CA certificates and Full
(strict) TLS. Origin keys are generated on the VPS and never returned by Sandbox.

Nested hostnames that are not covered by the zone's edge certificate can opt into
DNS-only public ACME instead. Caddy then obtains and renews a publicly trusted
certificate, while Cloudflare manages only the DNS record:

```yaml
cloudflare:
  proxied: false
  tls: acme
```

This mode exposes the origin directly and does not provide Cloudflare CDN, WAF, or
proxy-header guarantees. Consequently, `basic_auth.bypass_ips` is rejected in this
mode; ordinary Basic Auth remains supported. Wildcard routes are also rejected because
they require a DNS-01 challenge and remote DNS credentials; declare each exact hostname.

Then use `wp_cli`, `fs_read`, `visit`, `run_tests`, etc. through the `sandbox-myvps` MCP
connection exactly as you would through `sandbox` locally.

On a shared VPS that will also host other apps (for example, Next.js), route every public
app by hostname through Caddy. Sandbox's control endpoint should get its own hostname
(`sandbox-control.example.com`), and your Next.js app should get another
(`app.example.com`). Plain sandbox WordPress instances still use high ports; avoid
`domains setup` unless you intend sandbox to add public hostname routes for those sites
too.

### Disposable remote previews

`./sb preview` creates a separate, public WordPress Sandbox instance for a
temporary branch check. It uses a generated `preview-…` instance label, isolated
Docker containers/volume, a generated subdomain below `sandbox.asb.bd`, and an
expiry record stored only under `$SANDBOX_HOME/runtime/remote-previews.json`.

```bash
./sb preview create --remote myvps --project-dir /path/to/plugin --name fix-login \
  --ttl-hours 24 --confirm
./sb preview list
./sb preview destroy --remote myvps --id fix-login-<id> --confirm
./sb preview cleanup --remote myvps --confirm
```

Creation is confirmation-gated because it deploys the selected checkout, creates
one Cloudflare-proxied A record, configures a Caddy route, and starts a remote
container. `destroy` removes only the recorded Caddy fragment, named Sandbox
instance, and exact DNS record. `cleanup` removes previews whose recorded expiry
has passed; it is intentionally an explicit command, not a background reaper.

## 6. Explicitly out of scope (Phase 1)

- **No continuous sync daemon.** Deploy is deliberate and on-demand.
- **No VPS power management.** The VPS is persistent and user-managed — sandbox never
  starts/stops/hibernates it.
- **No multi-tenant / shared VPS.** One developer per remote target. Per-user isolation
  is a separate, much larger future effort (see `docs/remote-hosting-prd.md` §6).
- **No Herd.** Sandbox's macOS-native, Docker-less runtime has no remote equivalent —
  targeting a remote with a Herd-configured project fails cleanly (spec FR-014).

## 7. Security

### Operational trust boundary

Remote execution is for trusted project, plugin, and agent-generated code only. Docker
containers and workspaces on a remote target share the host kernel and Docker daemon;
this is not a hostile-code or multi-tenant security boundary. No per-instance
deny-by-default egress policy exists. Treat every remote `deploy`, `test`, `exec`, `ci`,
preview, and hosting workflow as trusted-code execution; separate workspaces do not
provide hostile-code containment or tenant isolation.

- In HTTPS mode, the remote MCP server binds only to `127.0.0.1`; Caddy exposes it as a
  TLS virtual host. In Tailscale mode, it binds only to the VPS's Tailscale address. It
  never binds to `0.0.0.0`.
- A per-remote bearer token (minted at `provision` time) is required on every request —
  enforced via a small Starlette
  middleware wrapping FastMCP's `streamable_http_app()` (FastMCP's own OAuth-oriented
  `auth=`/`token_verifier=` mechanism needs an issuer/resource-server setup that's real
  overkill for a single pre-shared secret between one client and one server).
- SSH connection strings are stored in `sandbox.local.yml`'s `remotes:` block — gitignored
  and `chmod 0600`. They are write-only: `remote add` accepts one, but CLI and MCP output
  never reveal it. The bearer token is also stored there; it is never shown, returned,
  stored in service metadata, or passed on an argument list.
- Remote streamable-HTTP is owned by `sandbox-mcp-remote.service`, a systemd user
  service with an owner-only environment file. It is restartable and reboot-recoverable
  only when user lingering is enabled. Sandbox refuses wildcard/public listener binds.
- A remote stop controls only the proven Sandbox service unit. Sandbox never scans or
  terminates generic streamable-HTTP processes by their command-line flags.

## 8. CLI + MCP surface

See `specs/014-remote-vps-hosting/contracts/cli-and-mcp.md` for the full flag/JSON-shape
reference. Summary:

| Command | Purpose |
|---|---|
| `./sb remote add <name> <ssh_url>` | Register a VPS target |
| `./sb remote list` | Show configured remotes + reachability + provisioned status |
| `./sb remote provision <name> --control-host <host> --confirm [--upload-timeout <seconds>]` | Fully automated install + start the remote MCP server over public HTTPS |
| `./sb remote provision <name> --control tailscale --confirm` | Same, but use Tailscale instead of public HTTPS |
| `./sb remote service status <name> --json` | Read-only owned-service, listener, and recovery evidence |
| `./sb remote service <name> --json` | Read-only status shorthand; equivalent to `service status <name>` |
| `./sb remote service migrate <name> --plan --json` | Read-only systemd service migration plan |
| `./sb remote service migrate <name> --confirm --json` | Stage the current Sandbox runtime, then install the protected owned service after explicit confirmation |
| `./sb remote service stop <name> --confirm --json` | Stop only the selected proven service unit |
| `./sb remote up` / `down <name> --confirm` | Legacy-compatible lifecycle entrypoints; planning is the default and migrated remotes use the owned service |
| `./sb remote remove <name>` | Forget locally — never touches the VPS |
| `./sb deploy --remote <name> [--deploy-timeout <seconds>]` | One-way, on-demand push of local state to the VPS with a bounded Git push budget |
| `./sb deploy --remote <name> --ensure --expose [--domain <host>] [--alias <host>]... [--prune-routes]` | One-shot deploy, boot/refresh and non-destructively reconcile the remote WP instance, activate the plugin, and expose a public HTTPS URL (plus any alias hostnames) |

MCP tool:
`remote_deploy(project_dir: str, remote: str, ensure: bool = True, expose: bool = True, domain: str | None = None, plugin_slug: str | None = None) -> dict`.
It mirrors `run_tests`/`run_plugin_check`'s calling convention and returns `instance`
plus `url` when exposure succeeds.

`remote service status` checks the selected unit's non-secret ownership marker and
runtime revision, expected bind/port, systemd activity/enablement, user linger, local
listener scope, and an authenticated `/mcp` probe. Its JSON evidence includes the
current `local_runtime_revision`, an `installed_runtime_revision` only when the
selected unit declares a valid non-secret digest, and `runtime_revision_state`
(`match`, `mismatch`, `unavailable`, or `unknown`). A configured service record is
not treated as proof of the installed revision. It treats unavailable evidence as
degraded; it never reads a credential into command arguments or output.

When an older PID-file-managed MCP process is detected, confirmed migration proves that
exact process's PID, working directory, bind, and port before handing it off. If the
new unit cannot start, its prior files are restored and only that proven legacy process
is restarted. No generic process search or termination is used.

Remote workspace list, status, migration planning, creation, reset, and destroy all
run the same read-only service preflight before sending a workspace request. The
selected owned MCP service must report `ownership=proven` and
`runtime_revision_state=match`; mismatch, unavailable, unknown, or unproven evidence
is refused without dispatching the workspace command. Refresh the service through the
supported lifecycle command, then retry:

```sh
./sb remote service migrate <name> --confirm --json
```

When a label matches more than one remote record, lifecycle control fails closed and
the JSON response includes the bounded candidate `workspace_ids`; retry with the
chosen opaque ID using `--workspace-id` rather than guessing from a path.

Confirmed migration also builds or repairs the staged Sandbox CLI and MCP virtual
environments before stopping a proven legacy process, so a runtime refresh cannot leave
the replacement service without its interpreter dependencies.

The migration archive intentionally excludes local-only generated payloads such as
`node_modules`, Electron release/build output, `.cache`, and the existing runtime
directory. These artifacts are not imported by the remote Python CLI/MCP service;
excluding them keeps the supported upload within its bounded transfer window. Project
source deployment remains a separate operation.

## Shared Git history and opt-in Node package storage

New remote job workspaces copy their worktree and mutable Git metadata privately. Eligible
content-addressed files below `.git/objects` are hard-linked when the filesystem allows it.
Cross-device, unsupported, and permission failures fall back to a complete private copy.
Old-layout workspaces remain valid and reset through their legacy lifecycle until they have
a materialization receipt; Sandbox does not migrate or delete them automatically.

Generic Compose projects may explicitly opt in with `compose.nodeStore: true` in their
project-owned configuration. Sandbox never infers this from package files or scripts. The
generated overlay mounts exactly one Docker-managed volume named
`sandbox-nodestore-<canonical-family>` at `/sandbox-node` and exports:

```text
SANDBOX_NODE_STORE=/sandbox-node/store
SANDBOX_NODE_MODULES=/sandbox-node/node_modules/<canonical-runtime-id>
npm_config_store_dir=/sandbox-node/store
```

Each runtime gets a distinct dependency-tree child while the package store remains
family-shared. The project remains responsible for pointing its dependency tree at
`$SANDBOX_NODE_MODULES` and removing any project-owned per-workspace dependency mount. The
BuildKit package cache is unchanged. A consumer that ignores these variables keeps its legacy
behavior. `compose.nodeStore: false` restores byte-identical legacy overlay generation.

Use this reversible migration order for one reviewed family:

1. Record the source revision, current overlay, named volumes, used-space observations, and
   source `git status --porcelain`, `git diff --exit-code`, and `git fsck --full` results.
2. Stop only the selected family, update its project-owned dependency-tree setting, set
   `compose.nodeStore: true`, and inspect the generated Compose configuration.
3. Start two disposable sibling workspaces. Confirm the same exact family volume, the three
   paths above, successful installs, and no store/module content on the host bind.
4. Keep the old layout until the measured cutover is accepted. Roll back by stopping the
   family, setting `nodeStore` false, restoring the project-owned dependency mount/command,
   and starting it again before considering reclaim.

Named reclaim is separate and confirmation-gated:

```sh
./sb resources plan --node-store-family <canonical-family> --json
./sb resources cleanup --node-store-family <canonical-family> --plan-id <plan-id> --confirm --json
```

Apply rechecks running mounts and removes only the exact planned name. A missing volume is an
idempotent `already_absent` result. Never infer a family, use wildcards, automate this from
ensure/status/destroy, or use broad volume pruning. Package data is repopulatable by a later
install, not backed up or losslessly recoverable.

## 9. Troubleshooting a failed `host apply`

**Read the error, not the exit code of a pipe.** `sb host apply` exits non-zero on
every failure, including a branch-gate refusal. Piping it to `tail`/`head` replaces
that status with the pager's, which reads as success — use `set -o pipefail`, check
`${PIPESTATUS[0]}`, or redirect to a file instead.

**Branch gates fire before any remote work.** `allowed_branches` is enforced per
environment, so a checkout on the wrong branch fails instantly with
`branch 'X' is not allowed for <environment>`. Deploy from the checkout whose branch
the environment allows.

**`insufficient remote disk for host apply`.** Before a confirmed apply, Sandbox
reads the remote filesystem metric through the authenticated diagnostics service
(or the registered SSH transport for older remotes). It requires the declared
`deploy.min_free_disk_mb` floor plus a 32 MiB rollback reserve. The reserve is
released after a successful apply and before DNS/Caddy rollback after a failed
Compose or health step, so a build that fills the disk does not immediately
remove the rollback path. Increase the manifest floor only after reviewing the
remote capacity; do not bypass the preflight with an ad-hoc SSH mutation.

**`failed to stat active key during commit: snapshot <id> does not exist`.**
BuildKit's snapshotter metadata is holding an active snapshot entry whose on-disk
directory is gone, so every cache-reusing build fails to stat it. `sb host apply`
now detects this and recovers on its own with a single `--no-cache` rebuild, which
regenerates the layer as a valid committed snapshot; later cached builds hit the
good one.

Diagnosing it by hand, if the automatic recovery is ever bypassed:

- The snapshot ids are **identical across runs**. Stable ids mean persisted
  metadata; a genuine in-flight race produces fresh random ids each run. This one
  comparison separates the two cases.
- `docker builder prune` does **not** clear it. The entry lives in
  `/var/lib/docker/buildkit/containerd-overlayfs/metadata_v2.db`, not `cache.db`.
- Ignore the accompanying `session healthcheck failed fatally: only one connection
  allowed` storm and `failed to read oom_kill event` warnings. Both are fallout from
  many build targets failing at once, not causes.
- Concurrency is not involved: a single-target build with no concurrency fails
  identically, and `COMPOSE_PARALLEL_LIMIT` does not serialize BuildKit's internal
  DAG.

Clearing the stale entry outright means stopping dockerd and wiping
`/var/lib/docker/buildkit`, which takes down every container on the host. The
`--no-cache` recovery is preferred precisely because it needs no downtime.

## 10. Known limitation / next step

**Live-verified against a fresh Ubuntu 24.04 VPS.** A real run against
`alim@212.47.72.49` installed Docker CE + compose, Caddy, the staged sandbox runtime, the
MCP venv, and the Playwright/Chromium tools venv. The remote now reports provisioned at
`https://sandbox-control.asb.bd`; Caddy owns public `80/443` by hostname, while the MCP
service itself binds only to `127.0.0.1:9174`. Bearer-auth probing reaches the app (auth
failures return `401`; the verified token now reaches MCP-level responses instead).
The HTTPS endpoint was then registered at its `/mcp` streamable-HTTP route and
successfully ran `fs_read`, `visit`, `wp_cli`, and `run_tests` against the VPS-side
`html-social-share-buttons` project. `specs/014-remote-vps-hosting/quickstart.md`
is satisfied for the public-HTTPS path.
### Runtime intent and remote instance recovery

The primary `sandbox.config.{json,yml,yaml}` file is deployed as a separate
runtime-intent layer even when it is gitignored or `deploy --source-ref` pins
an immutable commit. Machine-only `sandbox.config.override.*` files are never
transferred. Deploy and preview reconcile the resulting bind mounts before
activation, and activation refuses to create a symlink unless the selected
container can see the exact deploy target.

Remote instance inventory and teardown are explicit and name-scoped:

```bash
./sb instances --remote scaleway-sandbox --json
./sb instance delete <exact-instance-name> --remote scaleway-sandbox --yes
```

Use the inventory instead of guessing a runtime directory. Deletion targets
one validated instance name and lets the remote Sandbox CLI remove its own
runtime, volume, config block, and registry identity.
