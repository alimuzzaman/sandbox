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
```

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
```

Gateway installation enables systemd user lingering so an enabled gateway can
recover after a remote reboot; `hermes health` reports its linger state.

The local Sandbox MCP server also exposes `hermes_status(remote)` and
`hermes_run(remote, repo, prompt, worktree=true, async_=true)`, plus
`hermes_job_status(remote, job_id, offset=0)` and
`hermes_job_kill(remote, job_id)`. Async runs return a Hermes job ID; the
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
./sb hermes backup list --remote scaleway-sandbox --json
./sb hermes cleanup --remote scaleway-sandbox --dry-run --json
./sb hermes health --remote scaleway-sandbox --json
./sb hermes acceptance v2 --remote scaleway-sandbox --json
```

`update apply`, `backup restore`, and non-dry-run cleanup require `--confirm`.
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
