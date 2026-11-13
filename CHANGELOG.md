# Changelog

All notable changes to Sandbox are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- Queued durable jobs start oldest first across the host, whoever polls. A freed slot is offered to queued jobs when a job's supervisor finishes, on a status read or cancel, at the start of every job command, and every 20 seconds in the remote service. An older job blocked on its own workspace, a dependency or a sync generation does not hold up younger jobs, and `queue.position` counts only the jobs that start first. A status read never admits its own job ahead of an older one. The remote service also ends jobs whose supervisor is proved gone on that schedule.
- `jobs.scheduler` in a host's machine config sets its durable-job slot limit (`max_parallel`, 1-32, default 2) and free memory and disk floors (at least 512 MB and 2048 MB, the defaults). `sb job-policy [--remote R]` shows it and, with `--confirm`, writes it; the next admission uses it without a restart. `job-list` and `remote service diagnostics` report the limit and slots in use. Control protocol 23: remotes must be migrated to serve this controller.
- `healthcheck.deadline_seconds` (60-600, default 60) sets how long `sb host apply` waits for the health path.

### Fixed
- SSH sessions that do not share a connection, which includes every command over Tailscale, now send a keepalive every 10 s and end after 30 s without an answer. Before, ssh waited on a connection that died while the Mac slept or the network changed until the caller's own timeout ran out, which took 37 minutes once because that timeout does not count sleep. A remote command may still print nothing for as long as it runs. A Tailscale SSH check-mode browser login that stalls inside an existing session now ends about 30 s after its URL appears; Sandbox already treats check mode as denied.
- Joined `host exec` that loses its submission receipt, for example because the Mac slept during the submit, now looks up its own request ID in the server's job ledger once, within a 15 s budget checked between its two reads (a sleep during one read can stretch it, and a late reply is discarded), before reporting. If exactly one local server-cli job has that request ID and the same provenance and source the client sent, the job's result is read as usual; if that read fails, the client reports `acceptance: accepted` with the `job_id`. No match, more than one match, a different request or a failed lookup still reports `server_cli_exec_acceptance_unknown`. Nothing is submitted again. A job whose result cannot be read now hints at `sb job-status`.
- Joined `host exec` no longer spends about 80 s re-hashing the installed Sandbox source before running a command, so a `--timeout` under about 100 s no longer always times out. The server takes the target's lease only when the command, its kill grace and the lease release fit the deadline, clamping the command's timeout to fit or refusing with `host_exec_timeout` before taking the lease. The client adds those 25 s to its deadline, so the command gets the requested timeout less only the time before acceptance, and a job is no longer killed at its deadline between taking the lease and releasing it. A command that runs into its own `--timeout` still keeps the target's lease until the next exec takes it over (below). Update controllers together with the server: an older controller against this server refuses a `--timeout` of about 25 s or less. A lease held by another job is `host_target_busy` (exit 1) and a deadline that passes inside the job is `host_exec_timeout` (exit 124), instead of `host_exec_transport_failed` (exit 255). With `--name`, a missing-evidence refusal now hints at `sb host stack up` instead of `sb host classify`.
- Joined `host exec` takes over a target lease left by an earlier exec job, so a command that reached its own `--timeout` or was killed no longer leaves every later exec `host_target_busy` until the lease is removed by hand. The takeover happens only under the lease's own lock, only when the holder is the exec lease of a top-level server-cli job in the server's registry that is terminal, recorded its owned work stopped, ran against the same target and has no recorded process still running, and it is written to the host-exec audit with both job ids (a failed audit write prints a warning and does not stop the exec). It proves the job's own processes stopped, not a command Docker left running inside the container. Leases of applies, destroys and other controllers are never taken over, and an `interrupted` job never records the proof, so its lease is not taken over either. The `host_target_busy` message now says which leases need removal by hand.
- A durable job that ends by itself (`succeeded` or `failed`) now records `owned_work_stopped: true` in its `result_json`, as a deadline, stall or cancel stop already did. Before, only the supervisor's own stops recorded it, so the exec lease takeover above and `job-reconcile` refused an exec whose command reached its own `--timeout` (it exits 255 and ends `failed`). The supervisor records it only after checking, before it reaps the command, that nothing in the command's process group or session still runs, that no descendant it saw is still running, that nothing still holds the job's output pipes and, on Linux, that no process still carries the job's `SANDBOX_DURABLE_JOB_ID`. A same-user process whose environment cannot be read, such as OpenSSH's per-connection process, counts only when it started at or after the command, so an SSH connection the exec client opened earlier no longer denies the proof. If any of these fails, it records `owned_work_stopped: false` with the reason in `unproven`, and the job's lifecycle and exit code are unchanged. A process that left both the group and the session and also cleared its environment or changed user is not detected, the same as on the stop paths. Stack source repair also accepts a job that ended by itself as a stopped source consumer; it still scans for live processes itself.
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
- Current control protocol is **22**. Migrate runtimes from the upgraded checkout
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
