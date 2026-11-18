# Changelog

All notable changes to Sandbox are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- `sb notes` (`login`, `config`, `create`, `update`, `get`, `list`, `delete`, `sync`, `upload`, `share`) and `sb mockups` (`publish`, `update`, `list`, `get`, `pull`, `delete`, `share`) publish to an Agent Notes service from any shell, script, job or remote session, with no MCP client setup. The bearer token is held by the secrets broker (default `personal:SANDBOX_NOTES_TOKEN_<REMOTE>`) and used only inside a broker child process, so it never reaches argv, output, logs or config. `sb notes login` mints it through the service dashboard over tailnet trust, revoking an earlier token with the same name first. `sb notes sync DIR` publishes one note per markdown file, keeps a file-to-slug map in `DIR/.notes.json`, can be rerun and deletes nothing without `--prune`. A mockup is a folder with an `index.html`: stylesheets and scripts are inlined, images become note assets, and `pull` rebuilds the folder. `--remote` picks the service (default `notes.default_remote`) and every action takes `--json`. `login` and share-link creation refuse inside a durable job. See docs/agent-notes.md.
- Side-by-side runtime releases on remotes. `sb remote service migrate` builds each runtime revision in its own `$HOME/sandbox/releases/<rev>/`, with its own virtual environments and image staging helper, while the old service keeps serving. It then flips the `current` symlink and restarts the service, which takes about a second; `sb-src` stays as a compatibility link to `current`. The first install onto a plain `sb-src` converts it once, and that conversion can be rolled back. `sb remote service releases R` lists the releases. `sb remote service rollback R --plan|--confirm` flips `current` back to `previous` and restores that release's unit and receipts; running it again flips forward. Cleanup keeps `current`, `previous`, the newest 3, any release a live process uses and any release an unfinished job was admitted with. It also removes upload staging directories older than three hours. Controllers from before side-by-side releases cannot migrate a converted host. See docs/remote-hosting.md "Side-by-side releases".
- Remotes fetch the exact runtime commit themselves, anonymously over HTTPS from the GitHub origin, and verify it against the controller's archive digest. If the fetch fails they fall back to the upload, except on a timeout. Set `SANDBOX_RUNTIME_SOURCE_FETCH=0` to always upload.
- Zero-downtime stack applies (`deploy.strategy`, default `auto`). When a target is already serving, apply starts the new revision's stateless services beside it, health-checks them and moves the route there. It then recreates the base project and moves the route back. Stateful services stay single (`deploy.blue_green.shared`), and a service that mounts data must be listed in `deploy.blue_green.colored`. `in-place` restores the plain recreate; `blue-green` refuses instead of falling back. See docs/remote-hosting.md and docs/research/blue-green-stack-apply.md.
- `stack.mail_hostname` publishes a stack's Mailpit inbox as a tailnet-only name after every completed apply; it is never a public route. `stack down` and `stack reap --remote` remove it. `sb host tailnet-name ... --server-held` acts on the names a joined server holds, with the server's own authority.
- The panel's stack rows carry `page_path` (`/hosting/<remote>/<slug>`), and the controller row on a joined server shows as `this-server`.
- `sb ci run --as-runner` runs each workflow job as GitHub's runner user instead of root.
- `sb ci run --local --secrets-stdin` reads `NAME=value` secrets for one run from stdin, so a caller can hand over a credential it minted for that run without a command line, environment variable or config file. They override `sandbox.local.yml`, need no allowlist entry and all reach `act`, `GITHUB_TOKEN` included (what `${{ github.token }}` reads). Refused with `--async` and with a remote target.
- `sb host follow add|status|pause|resume|remove|run-now|key --remote R --name STACK` keeps a stack on a joined remote running the heads of declared GitHub branches with no controller online. A `systemd --user` timer on the remote polls every 5 minutes; when a branch moves it starts one durable server CLI job that runs the declared prepare step and the normal `host stack up` plan and confirm, and records the outcome. A busy target (lease or active apply) is skipped and tried again on the next poll, also when it becomes busy during an attempt; a failed commit is not retried until a new commit or `run-now`; and a stack that was taken down or reaped pauses its follow, even mid-attempt. Clones are checked out on the tracked branch, and a stack's lifetime follows the tracked branch of the repository holding its template, so an overlay commit built on `dev` can be the hosted `dev` stack. Every refusal prints one JSON line and is audited, and one follow's error no longer stops the poll of the others. `add` generates one read-only deploy key per repository on the remote and prints only the public key. Every command is audited on the remote. Migrate remotes to use it.
- Queued durable jobs start oldest first across the host, whoever polls. A freed slot is offered to queued jobs when a job's supervisor finishes, on a status read or cancel, at the start of every job command, and every 20 seconds in the remote service. An older job blocked on its own workspace, a dependency or a sync generation does not hold up younger jobs, and `queue.position` counts only the jobs that start first. A status read never admits its own job ahead of an older one. The remote service also ends jobs whose supervisor is proved gone on that schedule.
- `jobs.scheduler` in a host's machine config sets its durable-job slot limit (`max_parallel`, 1-32, default 2) and free memory and disk floors (at least 512 MB and 2048 MB, the defaults). `sb job-policy [--remote R]` shows it and, with `--confirm`, writes it; the next admission uses it without a restart. `job-list` and `remote service diagnostics` report the limit and slots in use. Control protocol 23: remotes must be migrated to serve this controller.
- `healthcheck.deadline_seconds` (60-600, default 60) sets how long `sb host apply` waits for the health path.
- `cloudflare.external_dns` lists declared hostnames of a DNS-only hosted target whose DNS is managed outside Sandbox, such as a CNAME to a CDN edge that proxies back to the origin. `sb host apply` serves them (route, ACME certificate, health) but never creates, updates, adopts or deletes their DNS records or refuses their foreign CNAME, and `sb host destroy` leaves their records alone. Plan, validate, apply and destroy output name them. Entries must be declared route hostnames; wildcards, `proxied: true` and `share.private` are refused. Stack templates may use placeholders in the entries.
- `recovery data` has an `infisical` profile for the Infisical PostgreSQL database of a Sandbox compose instance. It works like `lenzora-dev`: the client is the source container's own PostgreSQL, so it takes no credential reference and `client_image_id` must equal `image_id`. A restore is an isolated drill that can be inspected, verified and reopened, and `--target-volume` is refused. Its backup IDs must be `infisical-YYYYMMDDTHHMMZ` (UTC), so its sets sort apart from Lenzora's on the shared Drive destination. Its slot is `{remote}-infisical`, so it registers beside Lenzora sources on the same remote. The set holds only the database; Infisical's `ENCRYPTION_KEY` is not in it. The remote helper changed, so migrate remotes before using the profile.
- `recovery plan --remote R` also lists the PostgreSQL containers of Sandbox compose instances (`sandbox-*` projects other than `sandbox-host-*`) in `managed_containers`, `mounts` and `container_bindings`. Recovery drill and reference containers are left out.
- A hosted site without a stack can declare `ports: {mailpit: <port>}` on its environment in `sandbox.hosting.yml`, the fixed loopback port its own Compose publishes. After the next `sb host apply`, its card on the `sb web` Stacks & sites page shows the Mailpit upstream and **Open inbox** under the same managed tailnet-name checks as a stack. Ports in Sandbox's target, side-load and login-gate ranges are refused. Two stacks, sites or instances claiming one Mailpit upstream on a server link none of them (`shared_port`), and a declared port does not link while any of the server's target records or its instance registry cannot be read (`claims_unverified`).

### Changed
- A runtime install no longer waits for 0 active jobs. Each job supervisor runs in its own `systemd --user` scope, and each job runs from the release that admitted it, so installs and rollbacks restart the service under running jobs, and the new service re-adopts them. The install gate refuses with `remote_jobs_active` only over a supervisor still in the service's cgroup; on a host not yet converted, it refuses over every accepted, running or cancelling job. `--ignore-job ID` overrides a refusal for one job, and `--json` exits 1 on a refusal. In the release layout the gate releases job admission right after its check, so an install pauses submissions only for its check. A rollback does not stop running jobs: cancel them if they must not finish on the old code.
- `sb host tailnet-name` waits only on the caddy, edge and docker-pool locks; durable jobs never block it. certbot's deploy hook reloads nginx under the edge lock.

### Fixed
- `sb notes sync` no longer duplicates notes that already exist when `.notes.json` is missing or has no entry for a file. It looks the file up among your tailnet markdown notes by stored filename (the service's slugify of the file name), then by title. It adopts a match only when its content already equals the file, and otherwise fails the file with `notes_adopt_differs` (`--adopt` overwrites, `--create-duplicates` publishes anew) or `notes_adopt_ambiguous`. Public and HTML notes are never adopted. Notes that sync or `sb notes create --file` publish store the source file name as their download filename. A bare `sb notes login` with no selected service prints the full command to run.
- `sb host stack up` and `down` no longer refuse a slug for good after one refused apply submission. A refused `job-start` records as failed. A record left unsettled (including `submission_failed` records from older versions) is settled by the request id the job was admitted under: with no admission row and no job running from the stack's worktree, the slug is free; a recorded row is judged by its job; an unreadable job registry says to retry. A `submitting` record stays fenced for 300 s after it was written.
- `sb preview destroy` can be rerun after a delete step that ran and failed (a remote exit 1-123, or a Cloudflare 4xx or rejection). A timed-out or lost step stays unknown. `sb preview cleanup` skips a preview it cannot remove, removes the others and exits 1 naming the skipped ids. Previews created before custody fields existed are still never destroyed.
- `sb host exec` on a joined remote keeps polling after a failed observation of a running job (backing off to 5 s) and reports observation unavailable only once the original deadline passes.
- Host-local image provisioning, the image staging ledger, CI workspace cleanup metadata and the tailnet-name remote program no longer refuse a path below a traverse-only ancestor (such as `/home` at 0711, as on xCloud-style hosts): their descriptor walks open such a directory `O_PATH` instead of `O_RDONLY`.
- Lost-launch recovery no longer fails a `sb host exec` with stdin that is waiting more than 120 s for a job slot; such a job is left alone until its own deadline.
- A remote provisioned before the acceptance-receipt contract (its recorded capabilities predate it) no longer refuses every job after `sb remote service migrate`: when the recorded list lacks a required contract, Sandbox reads the installed runtime's `job-capabilities`, bound to the recorded marker and revision, and accepts on that.
- A job supervisor waits up to 60 s on a busy job registry instead of failing its job with `database is locked`.
- `sb host ...` commands sent to a joined server (`stack list`, `status`, `retire-delivery --name`, and others) report a failure that happened before submission as `acceptance: not_accepted`, with the refusal's own code, when Sandbox generated the request id. Examples are a working directory that is not a git checkout and a Docker network capacity refusal. Before, every such failure read `server_cli_acceptance_unknown` although nothing reached the server.
- `job-start`, `job-list` and the other remote job commands print a JSON error instead of a Python traceback when remote home resolution times out (`remote_home_resolution_timeout`), job capability discovery fails (`remote_execution_capability_unavailable`), a source lock is held (`remote_materialization_busy`) or a read-only job command times out (`remote_control_timeout`). `retryable` is true only where a retry can clear it.
- A stack apply's port probe checks only the applied target's own ports, and `sb delivery inspect` has a 60 s budget.
- A follow attempt runs in its own scope with umask 0022, so the oneshot tick no longer kills it and files it writes are readable.
- `sb server config` reading a fragment file on Linux: a file that could not be opened (a final symlink, a directory, a missing file) raised `AttributeError` on `errno.EFTYPE`, which exists only on BSD/macOS, instead of refusing with `fragment_source_unsafe`.
- `sb secrets ownership classify --check-destination` no longer times out on large profiles. It used the destination's per-request `timeoutSeconds` (30 s) as the deadline for the whole check, which sends one GET per mapped name, so a 146-name profile always failed with `operation_timeout`. The check now has its own `--timeout-seconds` (default 300, 1..3600, like `migrate`). The destination and its authority are unchanged, so no credentials need re-placing.
- `sb ci run` now finds a secret named inside a compound expression, such as `${{ secrets.FREE_REPO_TOKEN || github.token }}` or `secrets['X']`. Before, only a bare `${{ secrets.X }}` counted, so a workflow whose only secret had a fallback ran with neither and failed in its step instead of being refused before anything started.
- `sb ci run` no longer asks for the secrets of a step whose condition is exactly `${{ !env.ACT }}` (or `!env.ACT`). act sets `ACT`, so the step never runs there, and its secret blocked a run that does not need it.
- PostgreSQL recovery accepts schemas with more than 10,000 columns or constraints, such as the `infisical` profile's partitioned audit log. Observe and capture of that profile used to stop with `acceptance_unknown`. The 8 MiB schema size bound is unchanged.
- A failed `sb recovery postgres` helper run now says where it stopped: the error message and `error.detail` name the step, a fixed reason, the schema check or the failing command and its exit code. No rows, SQL errors or other output are reported. Other recovery messages stay fixed.
- `sb host login-url --remote R --name <stack> --confirm` (or `--target <project>/<environment>`) now works for a stack on a joined Tailnet remote. Before, it looked for the stack in the controller's own records, which the server keeps, and refused with `host_target_unknown`, or failed when a separate SSH session to resolve the remote's home timed out. The controller now makes the token and sends only its hash; the server resolves the recorded stack, installs the hash and records the issue, and the controller prints the link on its own terminal. The token never crosses the SSH channel, the link is never in argv, job output, a log or a state file, and both sides refuse inside a durable job. A runtime without the new contract is refused with a migrate hint before anything is issued. A connection that never opened, or a timeout, is retried up to three times with the same hash, so a retry cannot issue a second link. `host stack up` now reports the site URL and, for a WordPress autologin template, the `sb host login-url` command that prints the link once its apply succeeds. Control protocol 26: migrate remotes to use the new path.
- SSH sessions that do not share a connection, which includes every command over Tailscale, now send a keepalive every 10 s and end after 30 s without an answer. Before, ssh waited on a connection that died while the Mac slept or the network changed until the caller's own timeout ran out, which took 37 minutes once because that timeout does not count sleep. A remote command may still print nothing for as long as it runs. A Tailscale SSH check-mode browser login that stalls inside an existing session now ends about 30 s after its URL appears; Sandbox already treats check mode as denied.
- Joined `host exec` that loses its submission receipt, for example because the Mac slept during the submit, now looks up its own request ID in the server's job ledger once, within a 15 s budget checked between its two reads (a sleep during one read can stretch it, and a late reply is discarded), before reporting. If exactly one local server-cli job has that request ID and the same provenance and source the client sent, the job's result is read as usual; if that read fails, the client reports `acceptance: accepted` with the `job_id`. No match, more than one match, a different request or a failed lookup still reports `server_cli_exec_acceptance_unknown`. Nothing is submitted again. A job whose result cannot be read now hints at `sb job-status`.
- Joined `host exec` no longer spends about 80 s re-hashing the installed Sandbox source before running a command, so a `--timeout` under about 100 s no longer always times out. The server takes the target's lease only when the command, its kill grace and the lease release fit the deadline, clamping the command's timeout to fit or refusing with `host_exec_timeout` before taking the lease. The client adds those 25 s to its deadline, so the command gets the requested timeout less only the time before acceptance, and a job is no longer killed at its deadline between taking the lease and releasing it. A command that runs into its own `--timeout` still keeps the target's lease until the next exec takes it over (below). Update controllers together with the server: an older controller against this server refuses a `--timeout` of about 25 s or less. A lease held by another job is `host_target_busy` (exit 1) and a deadline that passes inside the job is `host_exec_timeout` (exit 124), instead of `host_exec_transport_failed` (exit 255). With `--name`, a missing-evidence refusal now hints at `sb host stack up` instead of `sb host classify`.
- Joined `host exec` takes over a target lease left by an earlier exec job, so a command that reached its own `--timeout` or was killed no longer leaves every later exec `host_target_busy` until the lease is removed by hand. The takeover happens only under the lease's own lock, only when the holder is the exec lease of a top-level server-cli job in the server's registry that is terminal, recorded its owned work stopped, ran against the same target and has no recorded process still running, and it is written to the host-exec audit with both job ids (a failed audit write prints a warning and does not stop the exec). It proves the job's own processes stopped, not a command Docker left running inside the container. Leases of applies, destroys and other controllers are never taken over, and an `interrupted` job is never taken over. The `host_target_busy` message now says which leases need removal by hand.
- A durable job that ends by itself (`succeeded` or `failed`) now records `owned_work_stopped: true` in its `result_json`, as a deadline, stall or cancel stop already did. Before, only the supervisor's own stops recorded it, so the exec lease takeover above and `job-reconcile` refused an exec whose command reached its own `--timeout` (it exits 255 and ends `failed`). The supervisor records it only after checking, before it reaps the command, that nothing in the command's process group or session still runs, that no descendant it saw is still running, that nothing still holds the job's output pipes and, on Linux, that no same-user process still carries the job's `SANDBOX_DURABLE_JOB_ID`. A same-user process whose environment cannot be read, such as an SSH server's per-connection process, is skipped when it started before the command. Otherwise its parent chain decides: a parent outside the processes that can adopt an orphaned job process (the supervisor's ancestors, the supervisor, the command and init) that started before the command proves it is not the job's, so a Tailscale SSH session that the exec client opens while the command runs to poll its status no longer denies the proof; reaching one of those adopters, a parent carrying the job's ID, a parent that cannot be read or a chain deeper than 64 still denies it, and the takeover then refuses as before. The environment scan stops after 2 s and then records the proof as missing, so a process stuck in the kernel cannot keep a job from ending. If any of these fails, it records `owned_work_stopped: false` with the reason in `unproven`, and the job's lifecycle and exit code are unchanged. A process that left both the group and the session and also cleared its environment or changed user is not detected, the same as on the stop paths. Stack source repair also accepts a job that ended by itself as a stopped source consumer; it still scans for live processes itself.
- Joined `host exec` now reports the server's own refusal of a target (`host_server_target_evidence_unavailable`, `host_target_busy` or `host_target_invalid`) with a hint, instead of the generic `server_cli_exec_unavailable`. Nothing is accepted and there is still no controller fallback.
- `host exec --target PROJECT/ENVIRONMENT` on a joined remote keeps working after a reapply. A succeeded apply moves an existing classified target's lifetime commit to the applied commit, without changing its lifetime or TTL, and `host classify` takes the commit, and the route hostnames when the summary lists any, from the server's summary of a succeeded apply instead of the controller's older history, which could record renamed hostnames and miss the primary one. Apply still creates no lifetime record: run `sb host classify` once for a non-stack target before exec.
- A refused autologin sign-in answers 403 instead of 500. A sign-in posted without the site's own origin now says "This sign-in must be opened in a browser from the stack page." so it can be told apart from a malformed or spent link.
- A hosted target with the managed `sandbox-login` gate (`share:` or `autologin.login_service`) and a `pushed_commit_sha` revision key now reaches ready. The gate receives each declared key from the apply's environment file, so its revision is proved like the application's services; an application service without the key still keeps the target out of ready. On a target with the gate, a revision key starting `SANDBOX_LOGIN_` (in any case) is refused. As before, every apply of a new commit recreates the background services, the gate included, so its share links and sessions reset.
- `sb wp` keeps piped stdin for the WP-CLI command instead of losing it to an internal probe. On a remote, with `--async` and under managed-native, commands that need stdin are refused up front, so `option update KEY` no longer risks writing an empty value.
- `sb apply` and `sb ensure` refuse a change to a server the project declares with `apply_server_change_refused`, and no longer revert a prior `sb server` when the config declares none. Use `sb server`, which does not yet verify or roll back.
- `sb snapshot list` refuses instead of saving a snapshot named `list`, and points to `sb snapshots`.
- `sb wp plugin install`, `theme install` and `media import` keep a local file's extension when staging it for the container, so WP-CLI recognises a local `.zip` or image.
- WordPress bootstrap readiness accepts the older `wp-includes/class-requests.php` layout on cores below 6.2, so a PHP 7.4 image no longer times out at 30 seconds on a site that is ready.
- `sb preview list` no longer prints the saved login URL, which can hold an autologin token, and honours `--remote`.
- The V2 reservation recovery snippet in `docs/remote-hosting.md` no longer fails on a host whose `python3` is older than 3.10.
- The dashboard and wp-admin refuse a second action on a busy instance with `instance_busy` instead of
  queueing it silently, except read actions and creating an instance. wp-admin can no longer delete a
  snapshot while a restore is reading it, which used to restore the database and silently skip uploads.
  Refusals are per `sb web` process. A sweep whose configuration cannot load now ends as failed instead of
  running forever.
- `host destroy` re-reads local state and makes its Cloudflare calls and state removal under the state lock, after the remote teardown. On one controller, a target that applies onto a record during teardown keeps it, reported as `kept_shared`.
- Recording a storage authority revocation refuses a row that names no remote, or neither a promotion nor a binding. Reading one that already exists still blocks authority.
- Dashboard and wp-admin snapshot jobs no longer mix output when one starts while another runs, and no longer leave the process's stdout pointing at a finished job. Bridge jobs now wait their turn like every other dashboard action, and a waiting job's console says so.
- `host destroy` reads `nothing to remove` once only images it does not own are left. The probe now counts the same `<compose-name>-*` tags teardown removes, and other labelled images are listed as kept, never removed.
- `sb ensure --remote` reports SSH failures as `remote_authentication_failed`,
  `remote_unreachable` or `remote_connection_lost` instead of
  `remote_empty_output`, naming the likely cause. Completion stays unknown: inspect before retrying.
- `host destroy` reads a DNS record saved with a complete snapshot before acting, and only touches it if its name,
  type, content, proxied status, TTL and comment still match what Sandbox last wrote and no other hosted target
  holds it. A record with an incomplete snapshot is left in place. Records saved earlier have no snapshot and are still acted on unread; the dry run lists every DNS
  action and marks those. `host apply` keeps first-adoption provenance across re-applies, so a record Sandbox
  created is deleted rather than restored, and a reused nginx Origin CA certificate keeps its ID.
- `job-*` control commands with `--remote` no longer write local Compose files first, so they run on a full
  local disk; `job-artifact-get --output-file` still needs local space for its file. Remote SSH commands no
  longer read the caller's stdin, so a `while read` loop around them processes every line.
- The `sb web` dashboard polls each job with one request in flight, so a slow response no longer
  duplicates output. A failed or stalled poll now ends and frees the dashboard instead of freezing
  refreshes until reload. Closing the drawer or starting another job no longer stops a job from
  reporting its end, and polling never resubmits a job.
- Storage authority capability evaluation never reports `proven` or adoptable until a protected
  writer provides a verified human review. A complete chain, one whose promotion names the requested
  remote, the same binding and both observed revisions and is unexpired and unrevoked, reads
  `implemented_unproven` with `acceptance_state: complete`. On a supported platform every named check
  reads `unknown`, except parity `fail` on drift, and the unobserved identity digests are null. Malformed values and unreadable revocations
  never count as a chain. Evaluation no longer writes lifecycle state, and unreadable state is refused and left
  in place instead of being reset, which used to erase recorded revocations; the capability check then
  fails with `authority_unavailable`.
- `sb visit` repairs a missing headless Chromium instead of failing before navigation
  when the browser cache was cleared, and install progress no longer reaches stdout.
- Repository guidance no longer contradicts itself: `CLAUDE.md` matches the read-only
  `main` rule, `specs/README.md` indexes specs 066-069, and release guidance no longer
  requires the `app.py` helper namespace.
- `sb secrets ownership` and scoped runs report an invalid local configuration as
  `input_invalid` (exit 2) instead of `provider_failed` (exit 4).
- `sb visit` reports the final document's status after redirects, so a redirect to a
  404 fails the visit, and a navigation during the login step no longer decides the
  result.
- `sb wp` stages host files for `plugin install`, `theme install`, `media import`
  and `eval-file` when WP-CLI global options precede the command, and the remote
  host-file refusal recognises the same forms. `eval-file` no longer rewrites
  arguments passed to the script.
- Explicit non-empty `SANDBOX_HOME` runs skip writing the legacy checkout-root
  `.env` compatibility file, preventing isolated state runs from rewriting shared
  checkout state while preserving existing output when `SANDBOX_HOME` is unset
  or empty.
- `sb secrets run` ordinary child timeouts now exit with status 124 after
  printing the bounded result instead of returning 0. Operator session lifetime
  expiry retains its separate contract.
- Retention sweep no longer counts no-op candidates as cleaned jobs. Those rows
  still consume the bounded scan budget; when `truncated` is true, callers must
  pass the returned `cursor` via `--cursor` to continue across processes. The
  envelope includes `cursor`, `limit`, and `truncated` fields (control protocol
  21). Retention safely handles malformed timestamps without
  crashing, reaches noncompleted terminal candidates with present `finished_at`
  timestamps so unindexed physical output is reclaimed under age and pressure
  sweeps, distinguishes completed cleanup from legitimate policy retention
  (which remains `ok: true`), handles per-row retirement and cleanup failures
  within sweeps while preserving fail-closed direct cleanup exceptions, and
  truthfully reports failed workspace releases and candidate errors with
  `ok: false` in both individual cleanup outcomes and the sweep result envelope.
  A CI archive removed before its retirement could be recorded no longer leaves
  the job failing every later sweep: retirement records its attempt first, and a
  later cleanup records the removal once the registry accepts the write. An archive
  missing with no recorded attempt still refuses.
- Remote diagnostics use stderr only, bound complete-line tails before decoding,
  and redact bare OpenSSH hosts/ports, usernames and remote paths before display
  truncation. No extra remote lookup or protocol change is introduced.
- Local-image remote-command failures preserve the exit status and bounded,
  redacted diagnostics instead of discarding the reconciliation error. Document
  the permission-only repair for legacy 0664 reservation ledgers; strict ledger
  validation and protocol 17 are unchanged.
- V2 readiness reads stdout only; SSH stderr warnings are captured separately
  in bounded diagnostics and cannot block a valid worker acknowledgement.
  Garbage on stdout still refuses before upload. Protocol 17 is unchanged.
- Local-image receipt publication strictly parses publisher acknowledgements
  with exact integer schema checking, duplicate key refusal, and bounded UTF-8
  frames; transport loss, subprocess timeouts, and malformed frames fail closed
  as typed publication-unknown without exposing diagnostic details.
- Shared-bundle reservations now start under the engine lease after proven
  orphans for that target are reconciled. A reservation-verified worker readiness
  acknowledgement precedes upload; early failures release their reservation.
  Persistent reservations remain for live workers and quarantined imports.
- Release accepts same-owner non-writable 0644 ledgers from older clients,
  tightens them to 0600, and waits boundedly for the ledger lock. Cleanup failures
  preserve the original error or successful publication and report pending cleanup.
- V2 builders require Docker >=25 and containerd. V1 archive cleanup is restored;
  ordinary reservation expiry is unchanged. V2 reserves at least 3x the full bundle
  with a default 1 GiB floor, scales load timeout, and fences uncertain imports.
- Control protocol 22 (current: 27, oldest served 1, `sandbox/remote_runtime/protocol.py`). Migrate runtimes from the upgraded checkout
  before bumping client pins: review `sb remote service migrate NAME --plan --json`,
  authorize `sb remote service migrate NAME --confirm --json`, and verify with
  `sb remote service status NAME --json`. Older runtimes cannot serve this client,
  including v1-only verdict-gated commands. No remote migration is performed here.
- Protocol history: 14 introduced v2 and recorded existing `cmd_retention` drift
  (`commands`, `remedy`, `skipped`); 15 added quarantine and 16 added persistent
  operation reservations. Their lifecycle corrections ship under 17; 18 added retention sweep cursor continuation envelope fields; 20 adds read keys for cleanup state kind and the structured per-candidate outcomes of retention sweeps (19 was never released); 21 adds the recorded CI archive retirement attempt. 22 records the read-only storage inspection payload.

### Added
- Opt-in local-image v2 receipts (`--receipt-version v2`) bind one shared raw
  save bundle, with closed image membership and version-aware apply readers.
  V1 remains the default and rollback format. The receipt contract introduced
  protocol 14; the current fixes above require 17. No installed runtime or
  deployment is changed by this source work.
- Hosted local images (spec 068). `sb host image build-local` builds an exact
  clean revision for `linux/amd64` with hosted Compose inputs and writes a
  bounded non-secret receipt; `host image push-local` confirmation-gates a
  registered-transport upload, skips exact remote images and publishes an
  immutable remote receipt. `host apply --local-images DIGEST` revalidates the
  source, target, Docker daemon and exact config/platform/layer identities
  before the existing hard no-build/no-pull lifecycle. A dedicated SSH-held
  remote-engine lock serializes load, verification and receipt-bound apply
  before delivery admission. Receipts retain a builder store ID separately from
  the portable archive configuration ID required on the destination, supporting
  Docker 29 containerd-store builders without digest aliases. The initial control
  contract used protocol 13. Apply also verifies the actual remote Compose render;
  local rendering refuses Docker-client-dependent image interpolation. Transfer
  pins registration, gates readiness before writes, validates target path slugs,
  caps every step with the aggregate timeout, and fences surviving load workers
  from subsequent transfer/apply owners.
- Secret-manager hardening binds each controller credential to the canonical
  project root and Infisical endpoint, exact selector, MCP grants and fixed consuming profiles;
  authority changes require explicit credential placement with no legacy-item
  migration. Provider OS-store reads/status, DNS and HTTPS work now shares one killable helper
  deadline, unsafe provider control characters fail closed, and brokered child
  groups are ended on every exit with wrapper/symlink/set-id escalation refused;
  supervised-session group cleanup is bounded below four seconds.
- Remote storage floor (spec 066). `sb remote set-storage-floor R --gb N` records free
  space that remote test/job work must leave for hosted sites; every remote deploy
  then refuses before any upload with `remote_storage_floor` (free, reserved,
  estimate and floor in the message) when it would cross it, or when free space
  cannot be measured. Concurrent submissions reserve their estimate for 45 minutes.
- `sb host heal --remote R [--target P/E] [--confirm]` lists hosted containers that
  exited after exhausting their on-failure restarts and starts exactly those, only
  while free space is strictly above the floor.
- Workspace reclaim removes the workspace's whole Compose project (containers,
  unshared volumes, networks) before its checkout, and treats a workspace whose
  retained jobs all finished more than 6 hours ago as a safe-tier
  `finished_job_idle` candidate (needs `sb remote service migrate`). CI job-cleanup
  uses the same stack teardown behind its identity checks and retains source and
  cleanup state if teardown fails.
- `sb host destroy --remote R --target PROJECT/ENV [--confirm]` removes one hosted
  target on the nginx front door: Compose containers, volumes, networks and its own
  image tags, edge route and certificates, remote
  runtime directory and generated secrets, the Cloudflare DNS records Sandbox
  created, the Origin CA certificate, and the local state entry; the deploy source, shared by the
  project's environments, is kept. It works from recorded state alone, dry-runs without
  `--confirm`, keeps state when any step fails, and refuses a Compose name shared
  with another recorded target. New target mutation capability `destroy`.
- Per-target hosting foundations (spec 060 Phase 1-2). A remote coordination
  program under `<remote SANDBOX_HOME>/runtime/hosting-leases` (one flock,
  owner-only files) keeps per-target leases with fencing tokens, a FIFO wait
  queue, holds of at most 4 h, build slots under a per-remote cap (default 2)
  and a 60 s remote-wide lease. The store lock wait is bounded at 10 s (a
  busy store refuses `lease_authority_unavailable`, reason `store_busy`), and
  a build slot lives as long as its renewed target lease (an unreadable or
  malformed target falls back to the slot's cached expiry). Coordination is not
  enabled by `sb remote service migrate`; enabling ships with the state
  conversion (T028). Until a remote is enabled, a coordination call refuses
  `lease_authority_unavailable`. Controller state for
  a remote converted to per-target files lives in
  `runtime/host-targets/<sha16>.json`; only changed targets are written, and
  a corrupt file affects only its own target, and the coordination listing
  reports a malformed or foreign record as unreadable. A malformed build slot
  still follows a live matching target lease, otherwise lives until its cached
  expiry (or is dropped without one); an unexpired malformed shared lease
  refuses `remote_record_unreadable`, and a recorded pid too large
  to probe keeps its fence. Remotes that are not converted keep using
  `hosts.json` unchanged. The control protocol is now 8.
- Docker-pool plan digest (spec 063 US4). `sb remote docker-pool` plans list
  the remote's hosted targets, the other running containers a daemon restart
  would also restart, a 16-hex `plan_digest` and the no-restart range
  alternative. `--confirm` now requires `--plan-digest D`
  (`docker_pool_plan_digest_required` otherwise), and an apply whose targets
  or container count changed since the plan is refused
  `docker_pool_plan_changed` with zero restarts. `--recover-interrupted` is
  unchanged and digest-free.
- Remote readiness (spec 063 US2). `sb remote readiness [NAME]` and the MCP
  tool `remote_readiness` report six rows (registration, reachability,
  runtime compatibility, capacity, ownership repair, handoff) under a 60 s
  deadline, each with a CLI remedy. Every remote submission, including
  `ensure --remote`, runs the check first and refuses on a `not_ready` row
  with `remote_not_ready_<aspect>` before any source byte is transferred; a
  passing proof is reused for 300 s at the same recorded runtime revision
  and generation (a migrate, provision, `up` or range assign rotates it before changing
  the remote, and refuses if it cannot).
  The refusal is an admission refusal: CLI `--json`, MCP tools and detached
  resource jobs return the `blocked` envelope with its `remedy`, and human
  output prints the remedy; this includes `ensure --remote` and the async
  MCP `run_e2e`/`ci_run` launches, and an unknown or unprovisioned remote
  (`remote_not_ready_registration`), also when MCP `run_tests` selects the
  project's remote automatically. `sb doctor` covers the project's declared remote, registered or not.
- Remote selection refusals (spec 063 US3). `unknown_remote` names the
  refused remote, whether the caller or `runtime.remote` chose it, and the
  registered remotes; `ambiguous_remote` (now a `remote_not_ready_registration`
  refusal) lists the candidates; refusals, readiness results and MCP
  submission results report `remote_selection`. MCP `run_tests` no longer falls back to a local run on an
  ambiguous remote or a remote without `job.exec`; pass `local=True`. An
  explicit local run while the declared remote is not ready states the remote,
  the failing row and its reason (CLI stderr, MCP `run_tests`/`job_start`
  result), and MCP `run_tests(local=True)` now passes `--local` to `sb test`.
  A local `sb e2e --async` or `sb ci run --async` coordinator is now launched
  with `--local`; before, it could select the project's declared remote.
- Development ranges on a remote (spec 063, first slice). `sb remote
  network-range propose|assign|list` proposes, plans or (with `--confirm`)
  records an operator range checked against Docker networks, host routes,
  Docker's default pools and CGNAT; `remote provision` reports a proposal and
  never assigns. Capacity admission now words a remote without daemon pools as
  `missing_pool_evidence` naming the range remedy, and its evaluator can count
  unallocated range capacity and refuse range exhaustion with a bounded
  allocation table and release commands. Control protocol 2 (range store) and
  3 (range fields in the admission refusal) wire them in:
  admission counts range capacity read-only, the runtime allocates one
  subnet per created network before a stack's first `up` (built-in and
  generic Compose including ownership repair, detached jobs, introspect,
  the MCP wp-cli fallback) and passes a subnet override, and instance teardown, `workspace reap` and
  retention expiry free the allocations (every reap sweeps unregistered
  instances, removing their networks first and retrying any still in use).
  Prepare, Compose and release are serialized per instance (a detached job's
  launcher inherits the lock until its Compose run exits); an allocation
  refusal during `apply` rolls back; exhaustion and collisions surface as
  typed codes, and the public admission refusal carries the allocation table
  and release-then-reap commands.
- Remote runtime coexistence (spec 061). Each runtime declares a control
  protocol range; `remote service status` reports `control_protocol` and a
  `compatibility` verdict, and remote consumers (workspace preflight, hosted
  apply eligibility, recovery, server capture, Postgres recovery, host memory)
  accept a different revision that serves this controller's protocol.
  `--strict-runtime` / `SANDBOX_STRICT_RUNTIME=1` keeps exact matching and
  holds an expiring pin on the remote; `sb remote pin list|release` manages
  pins, and a confirmed `remote service migrate` refuses over unexpired pins
  unless each is acknowledged with `--break-pin HOLDER`. A shape guard fails
  the test suite when transport payload keys change without a protocol bump
  (`python -m sandbox.remote_runtime.shapes --write` records them after one).
  Pin protection holds across the install itself: every install path runs its
  remote install under the pin flock, held until the install process exits
  (so installs never overlap, older controllers' registrations wait, and a
  dropped connection releases nothing); strict calls re-check the installed
  revision after registering; pins are capped at 64 per remote; and the shape
  guard fingerprints embedded remote programs and counted per-payload
  signatures named by function, binding site and nested key path, plus
  per-payload subscript writes and read key paths, on both the controller
  transports and the runtime's `sb workspace` / `sb job-*` / checkout
  response producers, plus key paths of real runtime receipts (jobs,
  artifacts, workspace status, checkout) sampled from those producers (manifest format 10). Strict refusals are honored by recovery, server capture and
  Postgres recovery even when the revisions match.
- `sb ensure`, `sb apply`, and `sb test`, plus their MCP counterparts, accept
  an explicit project-local `--config-file` selector when paired with
  `--project-dir`. Selection is fail-closed, owns one sibling descriptor
  family, preserves project-root-relative paths, and forwards remotely only as
  a validated project-relative path.

### Changed
- Remote control requests survive a control host that stops resolving: they connect
  to the recorded `origin_ipv4` with TLS still verified for the control hostname and
  warn, and without an origin the error names the unresolvable host (2026-10-12: the
  `.bd` registry dropped the `asb.bd` delegation and `sb-xcloud-london.asb.bd` went
  NXDOMAIN, failing lenzora deploy preflight with `recovery_target_identity_unavailable`).
- The local remote-registration lock is per remote: work holding one remote no
  longer blocks registration changes or listing of another, and a busy lock
  reports its holder (pid, command, start time). Mismatch refusals name the
  real remote in runnable remedies instead of `<name>`, `--remote NAME` or
  "sync the remote runtime revision".
- `sb` resolves its repo-local `.cli-venv` interpreter against the script's own
  directory instead of the caller's working directory, and also tries
  `python3.14`. Running `/path/to/sb` from another directory, including as a
  `job-start --local` command, no longer fails with "sandbox needs Python >=
  3.12" when only an old system `python3` is on PATH.
- `sb secrets run` text output labels the selected keys as `  keys=A,B`
  instead of printing each bare key name on its own line, so a key name can
  no longer be read as the first line of the child's output. A non-zero child
  exit now reports `child_failed: the command exited with status N; secret
  delivery succeeded` after the child's output, not before it.
- Feature 050 v2 image staging now reports the failed image role and a closed,
  redacted pull-failure class through terminal results and status replay without
  retaining Docker output; v1 staging remains unchanged.
- `sb apply --project-dir` / `apply_config` now reconcile WordPress core itself:
  a pinned `wpVersion` is installed exactly (`wp core update --version=<pin>
  --force`, upgrade or downgrade) and an UNPINNED project is moved to the
  current release, each followed by `wp core update-db` (`--network` on
  multisite). Core lives in the bind mount and survives every container
  recreate, so editing or deleting a pin previously changed nothing about a
  running instance. Non-fatal: a failed update warns and leaves the site on its
  current core. Reported as `wp_core: {from, to, changed}`.
- The version-drift warning on `ensure` now points at `./sb apply` (which
  reconciles both pins in place) instead of telling the operator to delete and
  recreate the instance.

### Docs
- `wpVersion` is documented as an EXACT build, not a version line: `"7.0"` is
  the 7.0.0 release and never tracks 7.0.4. Leave it unset unless one specific
  build is required; the `bug-repro` and `sandbox-cli` skills now say so at the
  point where a reported stack gets transcribed into config.
- Corrected the stale server/pin table in `docs/sandbox-config-reference.md`:
  `wpVersion` never enters an image tag on ANY server — every stack downloads
  core in-container.

## [0.2.2] — 2026-08-13

### Fixed
- Remote resource observation now converts a client-side SSH timeout into
  structured `timed_out` partial evidence instead of leaking a traceback or
  retrying an ambiguous probe.

## [0.2.1] — 2026-08-13

### Fixed
- Feedback list date filters now return RFC 3339 strings in JSON response
  receipts instead of leaking internal `datetime` objects to the encoder.
- Detached `job-start --json` acceptance is flushed as one complete line after
  durable acceptance, while replay-safe request guidance treats missing output
  as unknown rather than success.

## [0.2.0] — 2026-08-13

### Added
- Durable, owner-only workspace identity and migration index with byte-preserved
  legacy metadata, checkout-independent CLI/MCP controls, and plan-bound migration.
- WordPress PHP-extension requirements, deterministic image/package planning,
  four-plane verification, and rollback-safe apply behavior.
- Secret-redacted feedback detail/export/retention controls and conservative
  workspace-aware resource ownership diagnostics.
- A confirmation-gated `remote docker-pool` transaction that backs up and
  validates Docker daemon configuration, restarts Docker, recovers previously
  running containers, and reports only bounded host-capacity evidence.
- Restart-state-backed, confirmation-gated recovery for containers stopped by an
  interrupted Docker-pool transaction.
- A read-only `remote domains` inventory combining instance-registry URLs with
  Sandbox-owned Caddy routes without returning login tokens or route contents.

### Changed
- Remote job and workspace protocols now use canonical project identity, durable
  acceptance receipts, and strict top-level response envelopes.
- Remote deployment and hosting preserve exact nested source roots and immutable
  source provenance.
- Agents must verify local and installed remote revisions before depending on a
  changed CLI/MCP protocol.
- Remote revision receipts now cover every shipped CLI, service, workspace, and
  MCP Python source instead of a small proxy subset.

### Security
- Destructive restore requires explicit confirmation.
- Workspace migration is metadata-only and cannot authorize workspace destruction,
  network cleanup, or ambiguous ownership adoption.

### Fixed
- `job-list --active-only` now applies the active-lifecycle predicate before the
  bounded page, preventing older active jobs from disappearing behind newer
  terminal records.

## [Unreleased] — v1.0.0 target

### Fixed
- Local `job-status --json` now returns a stable `job_not_found` receipt with
  an explicit configured-remote retry hint instead of leaking a registry traceback.
- Confirmed remote provisioning now keeps an owner-only, redacted milestone
  journal and exposes incomplete prior attempts in the next plan instead of
  leaving a receipt-less update opaque.

### Added
- `./sb smoke` — self-test subcommand: boots a fresh instance, verifies WP + REST, tears down.
- `./sb doctor` now audits credential state (FluentBoards reachability, GitHub org set, `.env.local` permissions).
- `./sb doctor` now audits domain/proxy drift: Caddyfile readable inside the proxy container, and configured domain == Caddyfile route for every instance.

### Fixed
- Proxy compose mounts `runtime/proxy` as a directory instead of bind-mounting the
  Caddyfile as a file. The file mount pinned an inode; `regen_caddyfile()` replaces
  the file, so the running container lost `/etc/caddy/Caddyfile` and every
  `caddy reload` failed — `domains setup` reported "proxy container did not start"
  while the container was up, and new instances silently fell back to
  `http://localhost:<port>`.
- `proxy_apply()` self-heals: a failed hot reload now force-recreates the proxy once
  before reporting failure, and surfaces the real stderr instead of guessing at Docker.
- Secure-at-create rollback pops `tld` alongside `domain` and drops the orphaned
  Caddy route, so a failed proxy step no longer leaves an unrepairable half-state.
- `domains setup` wires new routes before deciding each site's URL, so freshly
  assigned domains reach WP's `siteurl`/`home` instead of staying on localhost.
- Snapshot database capture and restore now stream through host-owned `0600`
  files instead of bind-mounting snapshot directories, avoiding remote
  `Permission denied` without chowning paths to MariaDB's `mysql` UID or
  weakening global permissions.

---

## [0.1.0] — 2025-06 (per-project rewrite)

First feature-complete release. All major subsystems rewritten to the
per-project model: one `cd` + one `./sb init` gives any WP plugin repo
a live stack, isolated test DB, and a single registered MCP server.

### Added
- **Per-project config loader** — walks up from `project_dir` to find
  `sandbox.config.json` / `.wp-env.json`; deep-merges `.override.*`.
- **On-disk instance registry** (`runtime/registry.json`) — maps project
  root → instance (ports, server, version pins). `flock`-guarded writes;
  survives process restart. `registry_get/put/list/remove` API.
- **`ensure_instance(project_dir)`** — create-if-missing boot cycle:
  picks free ports, generates compose, boots, installs WP, wires plugins
  from the project config, records in the registry. Idempotent.
- **Single MCP server** (`sandbox mcp` / `./sb mcp`) — one registration
  replaces per-instance `sandbox-<name>` servers. Every tool takes a
  required `project_dir`; `ensure_instance` tool exposed.
- **External PHP test harness** — sparse-clones `wordpress-develop`
  tests at the instance's WP version, downloads phpunit 9 + polyfills,
  creates an isolated `wp_tests` DB. `./sb test` + `run_tests` MCP tool.
- **`./sb init`** — scaffold `sandbox.config.json` (or convert
  `.wp-env.json`), boot instance, provision test harness in one command.
- **Version knobs** — `phpVersion`/`wpVersion` in config resolve to
  server-aware image tags (apache, nginx-fpm, lsphp). WP CLI image
  follows the PHP pin.
- **npm / Homebrew distribution** — `@alimuzzaman/sandbox` package +
  `bin/sandbox.js` shim + `packaging/homebrew/sandbox.rb` formula.
- **`docs/sandbox-config-reference.md`** — full schema reference.
- **Web dashboard** (`./sb web`) — instance list, focus/server/start/
  stop/restart, per-project pointer for new instances.
- **Curses TUI** (`./sb dashboard`) — keyboard-driven instance manager.

### Changed
- Central `projects:` catalog removed; `./sb projects|pick|use|add` gone.
  Old code preserved at commit `25fc409` for reference.
- `instance create` removed from CLI (now `./sb init` per-project).
- `focus_set` / `focus_resolve` tools removed from MCP surface.
- README and CLAUDE.md rewritten to the per-project model.

### Fixed
- `instance delete` now cleans the registry entry (no more stale
  "ready" records pointing at deleted stacks).
- `cmd_update` / `cmd_doctor` / `cmd_status` re-pointed to the registry
  (no longer die on `main` not being in the catalog).
- `pretty permalinks` — `AllowOverride All` patch already in compose.
- Symlink depth — plugins land at `wp-content/plugins/<slug>` (depth 1).
- Bind-mount path — arbitrary-root plugin projects get their own mount
  so absolute symlinks resolve inside the container.
