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

## Private state repository

Hermes can keep a rebuildable, sanitized harness snapshot in a private GitHub
repository. Configure it once; subsequent setup runs restore the latest snapshot
and automatically publish any allowed local changes. Explicit sync is also
available:

```bash
./sb hermes state setup --remote scaleway-sandbox \
  --state-repo https://github.com/alimuzzaman/hermes-agent-state.git --json
./sb hermes setup --remote scaleway-sandbox --json
./sb hermes state sync --remote scaleway-sandbox --confirm --json
./sb hermes state restore --remote scaleway-sandbox --confirm --json
```

The snapshot includes the Sandbox Hermes integration metadata, resource policy,
Hermes state manifest, model/profile defaults, `SOUL.md`, and memory files when
present. It never includes provider or GitHub credentials, OAuth/session data,
cookies, private keys, checkpoints, logs, databases, worktrees, or runtime
binaries. Setup fails closed if the configured repository is unreachable or its
manifest contains forbidden paths. Authentication must be completed separately
on a rebuilt remote.

## Google Drive full recovery

Google Drive is the full recovery target; Git state sync remains the smaller,
sanitized configuration mirror. `drive backup` defaults to **full** scope:
Hermes chats, sessions, checkpoints, profiles, memories, skills, provider, Git,
and Drive credentials, managed repositories/worktrees (including uncommitted files),
Sandbox metadata, fresh WordPress database snapshots, and uploads. Docker image
layers, package caches, Hermes source/virtualenv runtimes, and sockets are
rebuilt rather than archived.

Configure `rclone` with a private Google Drive remote on the server. The
`drive.file` OAuth scope restricts it to files that rclone creates, including
its `hermes-full-recovery` folder. Rclone's interactive setup must complete the
Google OAuth login; then save only the remote name (not its token) in Sandbox:

```bash
ssh -tt alim@212.47.72.49 \
  'rclone config create gdrive drive config_is_local=false \
    scope=drive.file'

./sb hermes drive setup --remote scaleway-sandbox \
  --drive-destination gdrive:hermes-full-recovery --json
printf '%s' "$RECOVERY_PASSPHRASE" | ./sb hermes drive backup \
  --remote scaleway-sandbox --passphrase-stdin --confirm --json
./sb hermes drive list --remote scaleway-sandbox --json
```

The archive is compressed then encrypted with GPG symmetric AES-256 before
upload. The passphrase travels over SSH standard input and is never persisted,
logged, or sent to Drive. Keep it in a password manager: neither Drive nor the
server can recover a lost passphrase. Restore is deliberately confirmation-gated
and must first be exercised on a disposable replacement remote.

## Trust boundary

This is a trusted single-operator profile. Full Sandbox MCP access includes
destructive tools; Hermes terminal approvals are set to manual and dangerous
cron terminal actions are denied, but MCP access is not a separate
authorization boundary. Ask for explicit user intent before deleting, resetting,
restoring, exposing, or otherwise mutating Sandbox resources.

Secrets, provider credentials, OAuth values, and Git tokens are never passed as
`sb hermes` arguments and must never be printed. Do not copy a workstation
private SSH key to the remote. The broad GitHub CLI browser OAuth flow is
intentionally rejected because its minimum scopes include account-level
organization access.

Hermes installation fetches the selected annotated tag into a temporary Git
repository, verifies its SSH signature against the pinned upstream release
signer, confirms the exact commit, and executes the installer script extracted
from that verified commit. It never pipes a network response directly into a
shell. V2 backups archive the exact tracked Git commit object, its runnable
`venv` (and dashboard `.venv` when present), the integration-owned launcher,
and Sandbox-owned non-secret integration state and service units. Every tracked
source path is checked for credential-bearing filenames before packing; provider
configuration, credentials, sessions, checkpoints, and untracked files are
excluded. A normal restore creates a pre-restore recovery point; automatic
update rollback restores directly from its verified pre-update archive so a
partial failed installer cannot block recovery.

After a restore, Sandbox reapplies only its owned MCP/profile settings. This
returns direct `sb` and complete Sandbox MCP access without restoring provider
credentials, session history, or upstream authentication files.

## V1 workflow

```bash
./sb hermes doctor --remote scaleway-sandbox --json
./sb hermes install --remote scaleway-sandbox --version v2026.7.7.2 --json
./sb hermes setup --remote scaleway-sandbox

# Read a fine-grained token from stdin; it is not a command-line argument.
read -r -s GH_FINE_GRAINED_TOKEN
printf '\n'
printf '%s' "$GH_FINE_GRAINED_TOKEN" | ./sb hermes repo auth github \
  --remote scaleway-sandbox --token-stdin
unset GH_FINE_GRAINED_TOKEN
./sb hermes repo clone --remote scaleway-sandbox --url git@github.com:OWNER/REPO.git --name repo
./sb hermes run --remote scaleway-sandbox --repo repo --prompt "Inspect the test command" --async --json
./sb hermes job status --remote scaleway-sandbox --job-id JOB_ID --json

# Ad-hoc scheduler changes remain routed and confirmation-gated by Sandbox.
./sb hermes cron validate --remote scaleway-sandbox --json
./sb hermes cron create --remote scaleway-sandbox \
  --schedule '17 */4 * * *' --profile terra \
  --workdir /absolute/remote/worktree --name bounded-work \
  --prompt 'Process the next approved bounded task.' --confirm --json
./sb hermes cron route JOB_ID --remote scaleway-sandbox \
  --profile terra --confirm --json
./sb hermes cron run JOB_ID --remote scaleway-sandbox --confirm --json

# The repeatable path is the committed catalog. Preview first, then converge.
./sb hermes cron catalog --remote scaleway-sandbox --json
./sb hermes cron reconcile --remote scaleway-sandbox --force-replace --json
./sb hermes worktree list --remote scaleway-sandbox --json
./sb hermes repo sync --remote scaleway-sandbox --repo sandbox --confirm --json
./sb hermes gateway converge --remote scaleway-sandbox --json
./sb hermes gateway converge --remote scaleway-sandbox --confirm --json
./sb hermes cron reconcile --remote scaleway-sandbox --force-replace --confirm --json
./sb hermes cron verify JOB_ID --remote scaleway-sandbox --timeout 1200 --confirm --json
./sb hermes cron output JOB_ID --remote scaleway-sandbox --lines 200 --json
./sb hermes worktree inspect --remote scaleway-sandbox --name sandbox-approved-spec-task --json
./sb hermes worktree preserve --remote scaleway-sandbox --name sandbox-approved-spec-task --json
./sb hermes worktree preserve --remote scaleway-sandbox --name sandbox-approved-spec-task --confirm --json
```

The scheduler interface accepts only the named Sandbox routes `luna`, `terra`,
and `sol`. It resolves provider, model, and reasoning effort as separate values;
a string such as `gpt-5.6-terra/high` is invalid because `/high` is not part of
the model identifier. On the pinned Hermes release, reasoning effort remains a
profile setting while each default-profile cron job receives an explicit,
validated provider/model snapshot. `cron validate` is read-only. Creation,
routing repair, and triggering require `--confirm`, and creation always uses
local-file delivery rather than an external messaging destination.
Malformed or incomplete cron state—including a missing `jobs` collection or
non-object job records—is rejected as `invalid_cron_state`; it is never
silently reduced to an empty scheduler inventory.
Cron output responses likewise require the documented common fields and found-state metadata;
wrong shapes return `invalid_cron_output` instead of being coerced into a status.

The source of truth is `sandbox/hermes/cron-catalog.json`; its scripts live in
`sandbox/hermes/cron_scripts/` and are installed by setup, update, restore, and
confirmed reconciliation. Reconciliation without `--confirm` is read-only.
`--force-replace` backs up `~/.hermes/cron/jobs.json`, removes every observed
job, installs the committed scripts, and recreates exactly the reviewed catalog.
A partial failure reports removed and created IDs and retains the protected
backup so the same command can be rerun.

The base catalog keeps one bounded Lenzora TODO worker active. It reads only
repository-root `TODO.md`, advances at most one actionable `- [ ]` task per run
in a clean isolated Lenzora worktree, and reports `NO_TODO_WORK` when the file
is absent or complete. It can bypass an item only when the item explicitly says
that an unmet prerequisite blocks it; if every item is blocked, it reports
`REVIEW_REQUIRED` without a mutation. The quota requeue, Kanban dispatcher, and Sandbox Terra worker remain
disabled reviewed definitions: enable each only when its respective source of
work exists, then reconcile so its managed worktree is current. Spark remains
orchestration only; Luna remains read-only.

For the Lenzora TODO worker only, reconciliation makes Sandbox's committed
Spec-Kit templates, scripts, and Codex skill workflows available in the isolated
worktree when Lenzora's legacy `.Codex` command bundle is absent. These local
tooling files are excluded from Git status and never alter Lenzora's tracked
source merely to bootstrap the workflow.

## Authorization requests

When a configured scheduled task needs an explicit human decision, it creates a
structured pending request from its shipped authorization template. The cron
cannot select a different scope or origin, and it cannot approve the request.
The dashboard is review-and-approve only; it has no manual request form or
output-sync control. List and review requests before approving:

```bash
./sb hermes authorization list --remote scaleway-sandbox --json
./sb hermes authorization show REQUEST_ID --remote scaleway-sandbox --json
./sb hermes authorization approve REQUEST_ID --remote scaleway-sandbox --confirm --json
```

List and show are read-only: they may report an effective `expired` status from the timestamp,
but they do not write state or alter a cron job. The bounded expiry companion persists the
expiry transition and audit event.

Approval is default-deny: it only accepts an existing pending, unexpired
request and updates only the matching cron job's prompt with the reviewed
context. The injected context repeats its exact expiry and tells the worker to
stop and report `REVIEW_REQUIRED` at or after that time. The local
`authorization-expiry` cron runs every five minutes: it restores the committed
base prompt and records an `expired` audit event after an approval expires. It
also repairs a stale approved prompt after plugin deployment, without creating
or approving a request. Approval therefore lets the matching worker resume
only the reviewed dev scope until expiry; it does not create, run, remove, or
otherwise reconfigure jobs. Each lifecycle event is retained in the bounded,
secret-screened Hermes state audit; approving a newer request supersedes every
older approval for that same job, so only one approval can remain active.
Authorization approval persists the state with an optimistic digest under the
remote lock before updating the matching cron prompt. If another writer changes
the state first, the operation returns `state_conflict` before prompt mutation.
Before approval, Sandbox recomputes the request fingerprint from the stored job,
scope, replay origin, and rationale; a mismatched record is rejected before any
state write or prompt mutation.
If prompt delivery fails, the state transition is rolled back with a second
compare-and-swap, preventing an approved state from being left with an unrelated
prompt.
Malformed authorization timestamps fail closed as `invalid_state` errors rather
than being treated as an approval or leaking an unhandled parser exception.

The installed Hermes release does not retain cron output, so output scanning is
not used for authorization. A cron without a configured template reports its
blocker normally and never creates an authorization request.

Monitor scripts return zero only after a valid inspection or legitimate no-work
result. Missing files, malformed output, timeouts, and command failures return
nonzero, and scripts never add/remove their own cron job.

All Hermes controls use Sandbox's shared short-lived SSH multiplexing policy.
Sequential commands reuse authentication for up to 60 idle seconds, while each
operation retains a separate timeout, result, redaction boundary, and confirmation.
Hermes health already batches cohesive probes and can run independent probes over
parallel channels on that shared connection; operators should not concatenate
unrelated or destructive commands merely to save round trips.
Confirmed reconciliation creates the implementation worker's dedicated managed
Git worktree if it is absent; scheduled edits never target the primary checkout.
`hermes repo sync` refuses dirty or detached managed checkouts, performs only a
fast-forward merge, and for the `sandbox` repo atomically refreshes `sb-src`
from the committed Git tree while preserving its managed virtual environments.

`hermes health` aggregates gateway ownership, catalog drift, model routing,
bounded correlated request dumps, and dirty worktrees. A provider rejection wins
over an upstream `last_status=ok` marker and is reported as `false_success`.
`cron verify` likewise waits for a changed terminal run marker and rejects a
nominal success when correlated request evidence records an error.
`cron output` reads only the latest saved Markdown artifact for a validated job
ID, returns only its response/error outcome (never the stored prompt), bounds
the response, and withholds secret-like content. `worktree inspect`
returns a bounded diff only after a secret-like-content screen. `worktree
preserve` previews by default; confirmed preservation rejects untracked files,
requires the expected `hermes/<name>` branch and a clean `git diff --check`,
commits tracked reviewed changes, and pushes that explicit branch.
Confirmed preservation repeats the secret scan while holding a per-worktree
lock immediately before staging, closing the preview-to-commit race.

Hermes Quick Setup defaults to Nous Portal. A ChatGPT Plus/Pro account can
instead use the upstream OpenAI Codex OAuth provider on the remote:
`hermes auth add openai-codex --type oauth`. The subscription login remains
isolated in that Hermes account and is separate from the fine-grained GitHub
repository token above. Sandbox-owned setup defaults the model to
`gpt-5.3-codex-spark` with provider `openai-codex`; authentication is still an
explicit operator action.

Create that fine-grained token in GitHub with the repository's owner as its
resource owner, **Only select repositories** set to the one repository Hermes
may use, no organization permissions, and only the required repository
permissions. `Contents: Read and write` permits commits and pushes; choose
`Contents: Read-only` when Hermes only needs to inspect or test code. The
remote requires `gh` for this flow; on the supported Ubuntu host install it
once with `sudo apt-get install -y gh`. Git operations use HTTPS so this setup
does not upload or manage an SSH key.

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
./sb hermes gateway converge --remote scaleway-sandbox --json
./sb hermes gateway converge --remote scaleway-sandbox --confirm --json
```

Gateway installation enables systemd user lingering so an enabled gateway can
recover after a remote reboot; `hermes health` reports its linger state. The
managed unit starts with upstream Hermes's `--replace` flag so a stale/manual
gateway process cannot leave systemd in a restart loop or create two scheduler
owners.
The convergence operation also stops and disables the legacy
`hermes-gateway.service`, terminates only processes whose argv is an actual
`hermes gateway run`, installs the Sandbox unit, and verifies one owner. It is
idempotent after convergence.

## Routed worker profile

`./sb hermes setup --remote <name>` prepares a non-secret, multi-model Hermes
profile on every fresh remote. It does not authenticate a provider, check model
entitlement, install/start the gateway, or contact a messaging platform. Complete
provider authentication first, then inspect the profile roster with `hermes profile list`.
Sandbox-owned settings are merged atomically into the coordinator and every
worker profile; setup does not call the lock-prone upstream `mcp add/remove`
path. Remaining upstream CLI steps have named 45-second bounds so setup reports
the exact failed step instead of hanging silently.

| Role | Profile/model | Responsibility |
|---|---|---|
| Coordinator | default / Spark | Classify, delegate, collect evidence, and report risk. |
| Evidence worker | luna / Luna | Read and search files, logs, specifications, and public sources. |
| Implementation worker | terra / Terra | Bounded implementation, tests, formatting, and routine debugging. |
| High-judgment worker | sol / Sol | Architecture, specifications, security, authorization, data/API, and production-risk work. |

Direct `delegate_task` work uses Terra. The coordinator's Kanban policy provides
role-specific routing to the named worker profiles once the existing allowlisted
gateway workflow above is explicitly installed and started. Sandbox setup only
initializes the task board and its configuration; it never activates the dispatcher.

Luna receives Hermes's `safe` and `file` toolsets so it can inspect local evidence.
Upstream Hermes does not provide a read-only subset of the file toolset: `file` also
contains mutation-capable operations. Luna's policy prohibits writes, patches,
renames, commands, code execution, task creation, and external changes, but that is a
behavioral guard rather than a technical permission boundary. Route any needed change
to Terra or Sol.

### Reader.md on the operator workstation

Reader.md is installed only as an optional macOS operator aid, not as a Hermes
tool or a server dependency. A local agent may use `reader /absolute/path` to
open an already-known local Markdown file or folder for the operator to read.
The GUI is not agent-readable and cannot be used as verification evidence;
Hermes must continue using Sandbox file tools, repository reads, SSH output,
and tests for evidence.

Do not call `reader remote` from Hermes or an agent. It asks the local user to
create an SSH-backed, persistent Reader connection, which is outside a bounded
task. Do not call `reader rm`, which deletes that user configuration. Operators
may run `reader ls` themselves to inspect configured roots and can explicitly
run `reader remote user@host:/path` when they want a read-only remote folder.

The local Sandbox MCP server also exposes `hermes_status(remote)` and
`hermes_run(remote, repo, prompt, worktree=true, async_=true)`, plus
`hermes_job_status(remote, job_id, offset=0)` and
`hermes_job_kill(remote, job_id)`. Scheduler parity is provided by
`hermes_cron_list`, `hermes_cron_validate`, `hermes_cron_create`,
`hermes_cron_route`, `hermes_cron_run`, `hermes_cron_catalog`,
`hermes_cron_reconcile`, and `hermes_cron_verify`. Aggregated operations are
available as `hermes_health`, `hermes_worktree_list`, and
`hermes_gateway_converge`; mutating calls require `confirm=true`. Async runs return a Hermes job ID; the
equivalent CLI operations are `sb hermes job status|kill`. Returned output is
bounded and sanitized, including labelled and common bare provider/API,
OAuth, and cookie credential forms.

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
./sb hermes update provenance --remote scaleway-sandbox --version v2026.7.7.2 --json
./sb hermes backup list --remote scaleway-sandbox --json
./sb hermes cleanup --remote scaleway-sandbox --dry-run --json
./sb hermes health --remote scaleway-sandbox --json
./sb hermes acceptance v2 --remote scaleway-sandbox --json
```

`update apply`, `backup restore`, and non-dry-run cleanup require `--confirm`.
Sandbox verifies that the installed Hermes checkout retains the canonical
upstream `origin`; if Hermes's own updater reports a missing remote, do not run
an ad-hoc pull or reset. Use the verified Sandbox update workflow after the
remote has been repaired and its release plan reviewed.
`update provenance` is read-only: it fetches the requested signed tag into a
disposable remote checkout, verifies the configured SSH signer and exact commit,
then confirms the installed checkout is unchanged before and after the check.
`acceptance v2` is read-only: it reports the revision-bound evidence written by
the approved live recovery suite. It never offers an override or a way to set a
passing gate manually. Until every required check is recorded against the
currently installed Hermes commit and integration schema, every dashboard action
refuses without changing the remote. The wrapper exposes no `--insecure`
option.

After V2 passes, the dashboard wrapper creates an isolated remote virtualenv
for the pinned upstream Hermes web/PTY extras and manages a loopback-only user
service. Setup enables systemd user lingering so the dashboard survives SSH
session exit and reboot. Start/restart preflights an inactive port, waits for a
healthy loopback-only listener, and stops a failed launch. It does not create a
custom frontend or accept `--insecure`:

```bash
./sb hermes dashboard install --remote scaleway-sandbox --json
./sb hermes dashboard setup --remote scaleway-sandbox --port 9119 --json
./sb hermes dashboard start --remote scaleway-sandbox --json
./sb hermes dashboard doctor --remote scaleway-sandbox --json
ssh -N -L 9119:127.0.0.1:9119 alim@212.47.72.49
```

The service uses the same `$HOME/.hermes` profile and Sandbox MCP binding as
CLI Hermes. `status`, `logs`, `stop`, and `restart` are bounded systemd-user
operations. Public exposure is read-only by default and requires a normalized
FQDN, current V2 evidence, feature 015 managed-hosting support, OAuth/TLS
preflight, and explicit confirmation. Until those preconditions exist,
## Public dashboard route (`hermes.asb.bd`)

Public dashboard access is optional and uses this boundary:

```text
browser -> Cloudflare Access (exact identity + MFA) -> Cloudflare Tunnel
        -> Caddy on 127.0.0.1:9120 -> Hermes on 127.0.0.1:9119
```

The first release is **attach-only**: create the exact Cloudflare Access application,
narrow MFA policy, tunnel ingress, and DNS route outside Sandbox, then record their
non-secret references in the local `sandbox.local.yml`:

```yaml
hermes:
  public_access:
    account_id: "..."
    access_application_id: "..."
    access_policy_id: "..."
    tunnel_id: "..."
    zone_id: "..."
    dns_record_id: "..."
    access_token_secret: HERMES_CLOUDFLARE_ACCESS_TOKEN
    tunnel_api_token_secret: HERMES_CLOUDFLARE_TUNNEL_API_TOKEN
    zone_token_secret: HERMES_CLOUDFLARE_ZONE_TOKEN
    connector_token_secret: HERMES_CLOUDFLARE_TUNNEL_CONNECTOR_TOKEN
```

Secret values belong in the approved personal secret file, never this configuration,
a command argument, Git, state, or output. The Access policy must match only
`hermes.asb.bd`, contain an explicit narrow Allow rule, and require MFA. Tunnel ingress
must route only that hostname to `http://127.0.0.1:9120` and end with
`http_status:404`.

Review first:

```bash
./sb hermes dashboard exposure-status --remote scaleway-sandbox --json
./sb hermes dashboard expose --remote scaleway-sandbox \
  --fqdn hermes.asb.bd --plan --json
```

Only after reviewing current V2 evidence, the policy, tunnel target, secret references,
and rollback plan, and receiving explicit authorization for the remote change:

```bash
./sb hermes dashboard expose --remote scaleway-sandbox \
  --fqdn hermes.asb.bd --confirm --json
```

This command creates only the local loopback Caddy fragment and user `cloudflared`
connector service; it does not create, edit, or delete Cloudflare resources. If public
access is suspected to be unsafe, remove the local connector/Caddy route first:

```bash
./sb hermes dashboard unexpose --remote scaleway-sandbox --plan --json
./sb hermes dashboard unexpose --remote scaleway-sandbox --confirm --json
```

SSH forwarding remains the recovery route. Do not add `--insecure`, bind Hermes to a
public address, or treat optional Basic Auth as a substitute for Cloudflare Access MFA.

Optional Basic Auth is a second gate after Access. It is disabled by default and uses a
secret reference, not a password argument:

```bash
./sb hermes dashboard basic-auth set --remote scaleway-sandbox \
  --basic-auth-user operator --basic-auth-secret HERMES_DASHBOARD_BASIC_PASSWORD --confirm --json
./sb hermes dashboard basic-auth remove --remote scaleway-sandbox --confirm --json
```

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
# Hermes dashboard authorization plugin

Install the Sandbox-owned authorization tab on a compatible Hermes dashboard
without modifying Hermes itself. The installer registers the minimal
dashboard-only plugin in Hermes's `plugins.enabled` list; it grants no tool
overrides or agent tools:

On a loopback-only Hermes dashboard, Hermes validates its injected dashboard
session token before the plugin route runs. Because that mode has no named
principal, dashboard audit entries use the explicit actor `loopback-session`.
OAuth and Basic Auth dashboards retain their verified user identifier.

```sh
./sb hermes dashboard-ui install --remote NAME --confirm
./sb hermes dashboard-ui status --remote NAME
./sb hermes dashboard-ui upgrade --remote NAME --confirm
./sb hermes dashboard-ui uninstall --remote NAME --confirm
```

Sandbox-managed remotes derive their eligible jobs from the committed cron
catalog. A standalone SSH remote must supply a separate, non-secret catalog:

```sh
./sb hermes dashboard-ui install --remote NAME \
  --authorization-catalog /absolute/path/to/catalog.json --confirm
```

The tab only authorizes enabled catalog agent jobs. Every mutation stays behind
Hermes's existing dashboard session middleware; it records either Hermes's
verified principal or the explicit `loopback-session` actor in the local audit
trail. It adds no login, password, cookie, or second authorization service.

On dashboards whose plugin rescan endpoint is session-gated, installation is
reported as `pending_activation`; restart the existing Hermes dashboard service
when you are ready to make the new tab visible. Sandbox does not restart it
automatically.
