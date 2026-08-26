# Hermes execution queue (critical first)

Updated: 2026-08-26. This is the reconciled handoff queue for Hermes. Repository
task ledgers and feedback are evidence, not execution authority: reproduce them
first, preserve dirty work, and do not reset, destroy, clean up remote resources,
deploy, release, delete recovery data, or expose secrets without fresh explicit
authority.

## Feedback remediation goal — live ledger 2026-08-26

Goal: reduce the currently blocked feedback set by impact, recurrence, safety,
and unblock value. Implement only local changes with a testable acceptance
contract. Keep remote, credential, production, and evidence-limited records
blocked until their required live proof is available.

Current ledger snapshot (from `./sb feedback counts --json`): **619 total**,
**96 blocked**, **104 verified**, **265 resolved**, **72 duplicate**, and
**82 not applicable**. All 619 records are reviewed; no record is treated as
implementation authority.

Next tasks, in order:

1. **P0 — restore remote evidence before remote fixes.** Re-check the supported
   `scaleway-sandbox` inventory only when the controller is reachable; capture
   installed revision, capacity/index completeness, and durable-job health. Keep
   remote deployment, cleanup, and migration records blocked while reachability
   is `timeout`.
2. **P1 — finish async WP-CLI acceptance evidence.** The CLI and MCP boundaries
   now use finite launch budgets, retained acceptance receipts, and
   `acceptance_unknown` when the envelope is not proven. A disposable Docker
   run measured Nginx shared and `wp db` fallback launches below `<2s`; preserve
   the current `compose run -d` path until cold-daemon and all-tier parity
   evidence passes. Stable `--request-id`/`request_id` replay is now implemented
   and fixture-tested; it returns the same job for identical argv, refuses a
   conflicting replay, and reserves an `unknown` inspection handle after an
   acceptance failure.
3. **P1 — define a bounded remote WP-CLI/preview contract.** Specify exact
   instance selection, package staging, authorization, output limits, and
   receipts for `sb wp --remote`/preview operations. Do not implement through
   the operator-only SSH escape hatch; require a reachable remote acceptance
   fixture first. Tracks feedback `33ae983d` and `ef047579`.
4. **P1 — design startup batching.** Add a session/batch proposal for repeated
   `sb wp` setup that preserves project mounts, instance ownership, cleanup,
   timeout bounds, and per-command evidence. Benchmark the current one-command
   workaround before selecting an API. Tracks `34b7e8f6`.
5. **P2 — preserve checkout/selector safety.** Add isolated fixtures for
   detached HEAD, `--project-dir`, and remote/list scope. Only add a selector
   when its target-resolution contract and negative tests are explicit. Tracks
   `db90e71e`, `5bda94d7`, and `092ae3ad`.
6. **P2 — improve read-only guidance after contract review.** Document valid
   WP-CLI fields and project-mounted paths, and make E2E final-result reporting
   bounded and truthful only after a live or fixture reproduction. Tracks
   `6bf36b94` and `30123145`.
7. **P3 — resume runtime bug fixes with MCP proof.** For `feacbc91` and other
   runtime bugs, reproduce through the real Sandbox MCP surface, snapshot before
   DB mutation, apply the smallest fix, and rerun the identical call. Without
   that surface, status remains `blocked`, not `fixed`.

Sources reconciled in this pass:

- 59 unchecked rows in `specs/*/tasks.md`, plus explicit pending/missing live
  gates in checked convergence rows and implementation evidence.
- 305+ retained Sandbox feedback records (untrusted; many are duplicates or
  foreign-project observations), grouped below by owning behavior.
- `docs/release-readiness.md`, `docs/future-roadmap.md`, `specs/README.md`,
  `todo/README.md`, and the three product briefs under `todo/`.

## P0 — reliability, safety, and current operator blockers

- [ ] **Make remote durable-job acceptance and observation one replay-safe
  contract.** Persist the durable row before acknowledgement; return a
  non-empty canonical request/job ID; make request-ID lookup distinguish
  accepted, rejected, and unknown; make status/output/list/artifact paths use
  the same registry; retain complete bounded stdout/stderr and truthful
  `output_bytes`; never return empty-success or false-empty pages. Include the
  100 disconnect/reconnect proof in `specs/032-remote-job-runtime/tasks.md:T156`.
  Feedback: `343d1a5a`, `8b88c87e`, `2d168956`, `2e3b4d35`, `0384ab`,
  `3c4f9059`, `38efeba5`, `d7e6cba`, `dc88af80`, `1a7dde8b`, `00984500`,
  `00be19fe`, `25a58f04`.

- [ ] **Re-establish trustworthy remote capacity, ownership, and workspace-index
  evidence before provisioning or cleanup.** Re-run the supported inventory on
  the installed revision; retain unresolved/conflicting/foreign records;
  report complete versus bounded/partial coverage; include the Docker-pool
  rollback safety check (no stopped live containers); and keep
  `workspace_index_incomplete` fail-closed. Do not infer cleanup from names,
  age, or an incomplete scan. Feedback: `78aaf583`, `0fac3b07`, `bf05eeb9`,
  `a813480b`, `600d2def`, `0ed665d0`, `84585e00`, `01df389c`, `fc79f41e`,
  `088652d4`, `cd84b75d`, `b5ea1432`, `6a1cca01`, `3a6e8c1a`, `822262fe`.

- [ ] **Make target, transport, revision, and deployment truth actionable.**
  Separate registered reachability from brokered SSH/MCP usability; expose a
  bounded secret-safe host diagnostic/exec path; provide read-only hosted status
  with applied revision, lifecycle, health, and rollback state; preserve a
  durable deploy receipt after SSH/control loss; never exit 0 after a failed
  apply or silent rollback; refuse wrong-target/foreign-instance inference.
  Feedback: `b340f98a`, `834d2253`, `30a6c1d1`, `b8fcedf1`, `cc723c15`,
  `1ef4334d`, `b41513e9`, `ac945dff`, `b4323966`, `71be9430`, `0b420c9b`,
  `ccd9e5e2`, `7acb4245`, `e25a8491`, `4ad5d660`, `5e440951`, `f528a472`.

- [ ] **Prevent unsafe target and credential disclosure.** Prove explicit local
  selectors cannot route to a configured remote or an unrelated project; ensure
  status/ensure never emits autologin credentials; classify remote apply,
  rollback, and host reachability failures before mutation. Feedback:
  `19fe2251` (credential-bearing status JSON), `e8ab7717`, `0b420c9b`,
  `8371d7f7`, `a1fc66d4`, `1f094d2f`, `9f0122e7`, `d43d5bc4`, `ccd9e5e2`.

- [ ] **Repair the remote evaluator/bootstrap flow before benchmark claims.** A
  reproducible T120/T126-style run must show deterministic bootstrap, durable
  acceptance, retained terminal output, phase-aware classification, resource/
  OOM evidence, and a sanitized receipt. Feedback: `9bb7aea1`, `72022a6a`,
  `5978c11e`, `0eab73b8`, `6687abd9`, `74d503ab`.

- [ ] **Keep the launcher and execution environment deterministic.** The CLI
  must select a verified supported interpreter/venv on every invocation, and
  remote/deploy hooks must honor the repository Node pin before running package
  commands. Add regression coverage for the intermittent shell fall-through
  and missing-pyenv cases. Feedback: `2aa8e472`, `1ff68cf2`, `fdd88ab7`,
  `fb212649`, `1440ad3d`, `5fec1a2a`, `2c76137a`, `56b29b36`.

## P1 — active implementation and convergence work

### Remote/runtime and migration evidence

- [ ] **Async WP-CLI acceptance under 2 seconds** — the source path now reuses
  the running Apache/Nginx web container with `compose exec -d` when its
  built-in WP-CLI is present; DB/LiteSpeed/older or unavailable instances keep
  the `compose run -d` fallback. A private `acceptance_ms` receipt and finite
  launch deadline now separate acceptance from command output; CLI timeout is
  reported as `acceptance_unknown`. The 2026-08-26 disposable evidence records
  Nginx shared and `wp db` fallback paths below `<2s`; cold-daemon and
  LiteSpeed/older/stopped-service parity remain open in
  `specs/004-async-wp-cli-jobs/tasks.md:T021` before claiming SC-001. The
  duplicate-request contract is implemented and fixture-tested, but is not
  live-tier parity evidence.

- [ ] **Workspace relocation/migration proof** — complete
  `specs/009-runtime-user-dir/tasks.md:T042,T045` and
  `specs/035-resource-monitoring-cleanup/tasks.md:T056`: prove metadata/index
  transfer and base relocation preserve legacy bytes, locators, and all
  network/container/job/volume/upload/snapshot counts; keep conflicts visible.
  Also close the checked-but-missing runtime-user-dir obligations `T029–T034`
  (config-only migration, artifact regeneration, persisted home selection,
  guarded first-command migration, fixture/docs, and dry-run/force semantics).

- [ ] **Remote metadata/index acceptance** — record the read-only migration,
  relocation, checkout-independent controls, and unchanged job/container/network
  counts for `specs/032-remote-job-runtime/tasks.md:T170`; no cleanup proof may
  be inferred from partial evidence.

- [ ] **Dashboard DB reset/baseline live proof** — finish
  `specs/008-db-snapshots-reset/tasks.md:T016,T019`: capture new-instance
  `@install`/`install-baseline` only after final plugin/theme/seed onboarding,
  restart the supported bridge, and verify wp-admin reset plus polling. The
  pending bridge restart in `T013` and the live dashboard/seed evidence in
  `T021` remain open.

- [ ] **Full modular-boundary acceptance** — run the Hermes gateway/public route
  checks (`specs/022-sandbox-modular-boundaries/tasks.md:T086`), every
  remote/Hermes quickstart scenario without destructive restore/deletion
  (`specs/022-sandbox-modular-boundaries/tasks.md:T099`), and the
  focused/full suite, `./sb selftest`, `git diff --check`, and quickstart record
  (`specs/022-sandbox-modular-boundaries/tasks.md:T108`).

### Storage and workspace features

- [ ] **Finish scheduled storage-pressure monitor** — implement schedule
  rendering/activation with confirmation and fixed argv
  (`specs/043-storage-pressure-scheduler/tasks.md:T008,T009`),
  add `resources monitor|schedule` CLI flags and truthful renderers
  (`specs/043-storage-pressure-scheduler/tasks.md:T012,T013`), add schedule
  tests (`specs/043-storage-pressure-scheduler/tasks.md:T018`), update
  docs/README/CLAUDE/skill (`specs/043-storage-pressure-scheduler/tasks.md:T021,T022`),
  and run remote read-only dry-run/refusal evidence
  (`specs/043-storage-pressure-scheduler/tasks.md:T023`).
  Schedules remain disabled by default; no timer activation is implied.

- [ ] **Implement shared Git checkout materialization and opt-in node store** —
  complete `specs/044-shared-node-store-and-git-dedup/tasks.md:T001–T005` (safe plan,
  staged hard-link/copy fallback, remote rendering, reset integration, real
  filesystem tests), `specs/044-shared-node-store-and-git-dedup/tasks.md:T007–T008`
  (family derivation/overlay/tests),
  `specs/044-shared-node-store-and-git-dedup/tasks.md:T009–T012`
  (legacy/rollback/docs),
  `specs/044-shared-node-store-and-git-dedup/tasks.md:T013–T015`
  (bounded evidence and named reclaim contract),
  `specs/044-shared-node-store-and-git-dedup/tasks.md:T016–T018`
  (remote gates and confirmation-gated plan), and
  `specs/044-shared-node-store-and-git-dedup/tasks.md:T019`
  (focused suite/diff check). `T006` only normalizes the boolean; it does not
  prove the feature.

### Linux/native adoption proof

- [ ] **Ingress qualification and host proof** — complete
  `specs/037-host-ingress-adoption/tasks.md:T078–T080`: non-forgeable production
  qualification, default Docker/Caddy proof on Linux with free 80/443, and the
  listener/IPv6/failure/cleanup matrix.

- [ ] **Resolver qualification and host proof** — complete
  `specs/038-tld-dns-adoption/tasks.md:T067–T070`: non-forgeable systemd-resolved
  qualification, a supported Sandbox-owned default path (or approved scope
  change), plain-resolv.conf HTTP/DNS repeatability, and owner-change,
  wildcard-owner, and exact-name negative gates.

- [ ] **Managed-native Ubuntu proof** — complete
  `specs/039-native-runtime-adoption/tasks.md:T077,T080,T087`: run the full quickstart
  on a normally booted Ubuntu 24.04 host, restore the <=3-second managed-host
  preflight proof for primary/sibling/post-cleanup cases, and capture remote GD
  and content-addressed apply/cache preservation across web, WP-CLI, and
  PHPUnit. Local tests are not a substitute.

## P2 — protected recovery, hosting, and product gates

- [ ] **Real recovery set and fresh-server drill** — with explicit credentials
  and approval, complete `specs/023-scoped-recovery-profiles/tasks.md:T060,T061`; keep
  automation disabled until a current-passphrase encrypted set verifies and a
  disposable fresh-server drill passes Hermes/public-dashboard/hosting checks.

- [ ] **Recovery deletion/scheduling only after proof and authorization** —
  prepare the legacy Drive deletion plan
  (`specs/023-scoped-recovery-profiles/tasks.md:T069`), apply only the exact
  reviewed plan with explicit deletion authorization
  (`specs/023-scoped-recovery-profiles/tasks.md:T071`), and activate/monitor the
  non-overlapping schedule only with separate scheduling authorization
  (`specs/023-scoped-recovery-profiles/tasks.md:T072`).

- [ ] **Lenzora/authorization live gates** — keep the external work isolated and
  approval-bound: `specs/015-managed-hosting-cloudflare/tasks.md:T031` (reapply allowed
  dev revision and verify anonymous 401/authenticated 200),
  `specs/026-lenzora-todo-worker/tasks.md:T006` (canonical isolated Spec-Kit workflow and
  verified worker execution), and `specs/027-hermes-authorizations/tasks.md:T021`
  (catalog-companion deployment/reconciliation/pending-request acceptance).
  Also retain the signed upstream update-history proof in
  `specs/027-hermes-authorizations/tasks.md:T018`.

- [ ] **Historical Drive backup ledger is superseded, not active work.**
  `specs/018-drive-full-backup/tasks.md:T007,T009,T011,T012` remains unperformed, but the
  spec explicitly says it is superseded by Spec 023; satisfy the equivalent
  real-set, restore, ciphertext-only, and live-suite gates through
  `specs/023-scoped-recovery-profiles/tasks.md:T060,T061` rather than
  implementing a second backup path.

- [ ] **Release-readiness checklist** — before any release claim, satisfy the
  required gates in `docs/release-readiness.md`: doctor for every supported
  runtime/remote, MCP restart and changed-tool call, Herd parity, dashboard
  reset, focused/full tests, `./sb selftest`, and `git diff --check`.
  Every thin MCP wrapper must also use the shared `_run_sandbox_json` timeout /
  final-JSON / parse-failure contract and retain wrapper-specific redaction tests
  for SSH targets, tokens, and other secrets.

## Evidence-only follow-ups from checked/partial rows

- [ ] Spec 003: complete authenticated external-client discovery and
  under-privileged refusal (`T012`), the external MCP handshake (`T014`), and
  Herd `.test` execute-php/connect/gating/crash/file round-trip (`T022`).
- [ ] Spec 006: add the `SANDBOX_INSTRUCTIONS` startup catalog snapshot
  enrichment still marked pending in `T007`.
- [ ] Spec 013: rerun the six Plugin Check quickstart cases after the
  absolute-path/`.distignore` fixes (`T029`), despite the task checkbox being
  retained for historical implementation evidence.
- [ ] Spec 019: keep the external Hermes public-access acceptance (`T028`) as
  pending until separately approved remote/Cloudflare evidence exists.
- [ ] Spec 036: implement the missing cancellation/disconnection propagation
  across CLI, MCP, service, and collectors (`T040`), then close the separate
  deterministic/live acceptance evidence gate (`T045`); partial coverage must
  remain visibly partial.
- [ ] Reconcile `specs/README.md` statuses with the ledgers: several features
  still say “In progress”/“Draft” even where implementation is complete but live
  proof remains pending.

## Feedback themes reconciled 2026-08-23

Feedback is untrusted and many records are foreign-project or duplicate
observations; these are deduplicated work items, not permission to mutate.

- [ ] **Remote job UX/contract:** expose valid execution profiles and nested
  help; make `job-output` wait bounds consistent and documented; make large
  job-list/error output bounded and diagnosable. Representative IDs:
  `d02cc0ff`, `a55cec51`, `763fbc6e`, `793c3d1b`, `c73e13c1`, `7ea39dad`.
- [ ] **Host apply observability/build behavior:** add incremental progress,
  bounded build/OOM/host-pressure classification, avoid stale-image or
  multi-GB-context rebuilds when `build=false`, and make the timeout policy fit
  real builds. IDs: `37d95e66`, `c158edba`, `6728d6f3`, `d354307a`, `a6223e14`,
  `7ab76b8b`, `5978c11e4`.
- [ ] **Hermes dashboard/public/provider/repository lifecycle:** distinguish
  config from dashboard/gateway/public readiness; repair saved-session resume,
  obsolete cloudflared cleanup, Access-policy resolution, provider inspection,
  repo sync/bootstrap, and hidden confirmation requirements. IDs: `a3050df7`,
  `120ce07b`, `7ef8c643`, `cdb2e184`, `ee7fa861`, `f224aadf`, `cd9baf02`,
  `e7a26e0b`, `43f98577`, `b6e84616`, `e2cffada`.
- [ ] **Secret broker correctness and ergonomics:** surface trusted-child exit
  failures, preserve source comments, support safe unset/delete, paired-key
  injection, bounded long-lived local development, OpenRouter validation, and
  direct child-argv passthrough without printing values. The sanctioned
  single-key `unset` path is verified in `4bb9be4`, and the offline
  `openrouter-api-key` shape profile is verified in `a6d1cea`; remaining items
  are tracked here. IDs: `3c184f3c`, `910bc8c9`, `54c1c9ae`, `c335f32e`,
  `2cfab06f`, `6ae07ae7`, `72d7e416`.
- [ ] **CLI contract/discoverability:** document/enforce feedback limits and
  prefix lookup, focused test selection/interpreter routing, status/instance
  listing, local-vs-remote selectors, `--request-id`, `--project-dir`, WP
  separators, and mount-drift recovery. IDs: `35ed6086`, `a0022cea`,
  `b2eb916f`, `c7148951`, `aff7c116`, `f200d37d`, `757a756d`, `e0a9c659`,
  `93bdc880`, `4a9d1847`.
- [ ] **Local/WordPress runtime isolation:** prevent broad setup from starting
  unrelated instances, preserve explicit project association, fix stale mounts
  after config changes, provide a supported remote-preview WP-CLI path, and
  classify core/install/inspection hangs. IDs: `9f0122e7`, `20a25084`,
  `103ae36f`, `fda8e3c5`, `ef047579`, `92966e70`, `d43d5bc4`, `f13ce98a`.
- [ ] **Clean URL/proxy and host repair:** make ingress-down states fail fast,
  make dead proxy diagnostics actionable, and avoid false-negative HTTPS
  reachability/rollback. IDs: `550d07ec`, `98989848`, `441022bf`, `7acb4245`.
- [ ] **Feedback service robustness:** preserve valid JSON on paginated/since
  responses and typed errors on malformed records. The current pass recorded
  `e8ddb411` after a local `jq` parse failure; later JSONL pagination completed,
  so the failure remains historical/unverified until reproduced.

## Deferred product discovery (do not implement silently)

- [ ] **Release Guardian (Phase 0):** resolve the five owner decisions in
  `todo/00-wordpress-plugin-release-guardian/prd.md` (security scanner/rules,
  WP/PHP matrix, baseline mutation authority, trace/privacy/cost retention,
  first design partner/policy), then run `speckit-refine` and an independent Sol
  High readiness review before specification.
- [ ] **Outbound mail (Phase 1, deferred):** resolve mail hostname, DKIM scope,
  recipient allowlist, DMARC-relaxation authority, and permanent-site send
  default in `todo/01-outbound-mail/prd.md` before Spec-Kit conversion.
- [ ] **Herd-equivalent stacks (Phase 2, NOT READY):** resolve the five choices
  in `todo/02-herd-equivalent-polyglot-stacks/prd.md` (equivalence promise,
  frontend strategy/threshold, Apple-Silicon MySQL, environment scope, and
  related-project ownership), then use the canonical Spec-Kit sequence.
- [ ] **Agent-aware remote sync and Google Drive backup PRDs:** resolve the
  consequential choices/open questions and obtain the required Sol High
  readiness verdict in `specs/033-agent-aware-remote-sync/prd.md` and
  `specs/034-google-drive-backups/prd.md`; both remain `NOT READY`.
- [ ] **Config subdirectory discovery:** `specs/042-config-subdirectory/prd.md`
  is still discovery-only; convert it through the approved Spec-Kit workflow
  before implementation and preserve the move-together/ambiguity safeguards.
- [ ] **xCloud API adoption:** `specs/040-xcloud-api-adoption/prd.md` is
  explicitly deferred by the owner and remains `NOT READY`; do not advance it.

## Low-priority regression cleanup

- [ ] Fix the plain-environment MCP/PHP skip behavior, resource-reclaim probe
  regression, controller-only `structuredClone`/closed-list schema seams, and
  add the bounded-edge-capture invariant test (review findings 2026-08-22).
- [ ] Reconcile the remaining baseline/full-suite failures before release:
  `tests/test_mcp.py`, `tests/test_spec003_discovery_guidance.py`,
  `tests/test_resource_reclaim_service.py`, and the three baseline failures
  recorded in feedback `74d503ab`.
- [ ] Keep future roadmap items visible but separate from current release work:
  remote hosting V2 (portable provisioner, lifecycle UX, shared-VPS port policy,
  authenticated automation surface), dashboard parity, opt-in telemetry, and
  MCP hot reload (`docs/future-roadmap.md`).

## Lower-priority runtime and provider research

The evidence and decisions are recorded in
[`docs/v8-isolates-and-managed-sandbox-research.md`](docs/v8-isolates-and-managed-sandbox-research.md).
These items are discovery/RFC work only; they do not change the Compose default,
promote the unproven native adapter, or authorize a provider deployment.

- [ ] **Qualify a gVisor/Sandbox-v2-like managed-native profile.** Measure
  syscall compatibility, browser startup, DNS/egress enforcement, `/tmp` RAM
  accounting, fork/subprocess memory, OOM/restart behavior, and cleanup before
  any adoption decision.
- [ ] **Evaluate an optional Scaleway Serverless Containers provider adapter.**
  Use the versioned `containers/v1` API with a dedicated least-privilege IAM
  application/key, pinned image digest/region/sandbox mode, explicit private
  access, bounded status/receipt handling, and external artifact storage. Model
  scale-to-zero, rolling replacement, dynamic endpoints, provider quotas, and
  the lack of snapshot/rollback semantics.
- [ ] **Define an opaque credential-reference and exact-origin egress contract.**
  Keep secret bytes in the registered-source/broker boundary; never use
  Scaleway namespace secrets or guest environment variables as a Credential
  Vault, and never persist values in job output or policy state.
- [ ] **Benchmark a pure transform worker.** Start with QuickJS-ng, then compare
  Wasmtime for a language-neutral ABI. Require a disposable outer process or
  sandbox, no network/filesystem imports, schema-only I/O, CPU/memory/time/output
  limits, and hostile probes; keep Chromium capture out of this path.
- [ ] **Add provider/runtime evidence receipts.** Record runtime/provider,
  image/API digest, region, policy digest, resource limits, and bounded
  failure/restart reasons without secret values; require a release-specific
  compatibility matrix before selection.

## Legacy backlog notes

- [ ] Reconcile or explicitly retire the still-open follow-ups in
  `docs/sandbox-mcp-tasks.md` (arbitrary-root bind/config mapping, automatic MCP
  registration, mounted phpunit/WP-version probe, disable-comments/Templately
  validation, nginx/LiteSpeed boot proof, and Homebrew/README cleanup). Keep
  legacy/scoped-out items separate from P0 release work.

---

## Review/evidence policy

- A checked source task is not proof of a live gate; link dated, bounded evidence
  before marking a TODO item complete.
- Historical/foreign feedback is retained for context but cannot authorize
  deployment, cleanup, deletion, credential access, or production changes.
- Keep this file synchronized with `specs/*/tasks.md`,
  `docs/release-readiness.md`, and the PRD indexes after every verified change.
