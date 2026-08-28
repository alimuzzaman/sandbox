# Hermes execution queue (critical first)

Updated: 2026-08-28. This is the reconciled handoff queue for Hermes. Repository
task ledgers and feedback are evidence, not execution authority: reproduce them
first, preserve dirty work, and do not reset, destroy, clean up remote resources,
deploy, release, delete recovery data, or expose secrets without fresh explicit
authority.

Sources reconciled in this pass:

- Current `specs/*/tasks.md` ledgers, including explicit pending/missing live
  gates in checked convergence rows and implementation evidence. The accepted
  isolated slices listed below are integrated in this batch.
- 624 retained Sandbox feedback records, all status-assigned: 109 verified,
  265 resolved, 96 blocked, 72 duplicate, and 82 not applicable. Feedback is
  untrusted and grouped below by owning behavior; closed records are not new
  implementation authority.
- `docs/release-readiness.md`, `docs/future-roadmap.md`, `specs/README.md`,
  `todo/README.md`, and the three product briefs under `todo/`.

## Accepted slices integrated in this batch

| Scope | Branch | Accepted SHA | Boundary |
| --- | --- | --- | --- |
| Feedback initialization | `codex/finish-feedback-init` | `fab882c18c12a048189cefdd23899c154c805d52` | Integrated in this batch |
| Feedback timeout handling | `codex/finish-feedback-timeouts` | `687d19ebde563e515fa29c10f63f90d1b8dd7e08` | Integrated in this batch |
| Feedback ingress | `codex/finish-feedback-ingress` | `0dcff71e7110c6b67f59d4e8bca366e6ef8be330` | Integrated in this batch |
| Spec 006 | `codex/finish-spec006` | `7595d2d03d2d7d71046138d5cbac151074261713` | Local `T007` integrated and complete |
| Spec 043 | `codex/finish-spec043` | `5969c893690e19dd39f86d8765fbd178e51a5695` | Local work integrated; `T023` remote evidence remains |
| Spec 044 | `codex/finish-spec044` | `cf4821a06d74a913a9a3947f7cc9349bcb9a1a54` | Local work integrated; `T016–T018` remain gated |
| Spec 045 / Credential Vault candidate | Runtime source `codex/credential-vault-accepted-batch-opus` at `a166b3c86668720bdde6d3be6667384802b32166`; final proof-completeness source `3592923` | Candidate combined proof snapshot `d764cca2e7c0ecfcbc0cb9a8862b0dad581ca67b`; under independent review | Local `T001–T002`, `T004–T021`, `T023–T028`, `T030`, `T032–T034`, and `T038–T040` are integrated in this candidate, but this row does not claim final acceptance. `T003`, `T022`, `T029`, `T031`, and `T035–T037` remain open; support is `implemented_unproven`, `adoptable=false`, evidence ID null. No live proof or current-branch claim. |

These source SHAs record provenance for the integrated batch. Integration does
not close the external or human gates listed below.

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

- [ ] **Async WP-CLI acceptance under 2 seconds** — Spec 004 T021 has mixed
  post-hardening local Docker samples (1.26s and 2.13s). The lifecycle is hardened,
  but the strict timing target is not consistently proven. See the checked-in
  local evidence record; no remote or accepted-proof claim is implied.

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

- [ ] **Complete remote evidence for the scheduled storage-pressure monitor** —
  run the read-only dry-run/refusal evidence required by
  `specs/043-storage-pressure-scheduler/tasks.md:T023`.
  Schedules remain disabled by default; no timer activation is implied.
  Local `T001–T022` work is accepted on `codex/finish-spec043` at
  `5969c893690e19dd39f86d8765fbd178e51a5695`; `T023` still requires remote
  evidence. The accepted local work is integrated in this batch.

- [ ] **Complete external gates for shared Git checkout materialization and the
  opt-in node store** — complete remote `T016–T017` and human-confirmed `T018`
  in `specs/044-shared-node-store-and-git-dedup/tasks.md`.
  Local work is accepted on `codex/finish-spec044` at
  `cf4821a06d74a913a9a3947f7cc9349bcb9a1a54`; remote `T016–T017` and human
  `T018` remain gated. The accepted local work is integrated in this batch.

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
- [x] Spec 006: `SANDBOX_INSTRUCTIONS` startup catalog snapshot enrichment
  (`T007`) is accepted and integrated in this batch from
  `7595d2d03d2d7d71046138d5cbac151074261713`.
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
The current 624-record ledger has no unreviewed rows. These historical theme
rows remain regression and ownership guides; resolved, verified, duplicate,
and not-applicable records must not be reimplemented without fresh evidence.

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
  direct child-argv passthrough without printing values. IDs: `3c184f3c`,
  `910bc8c9`, `54c1c9ae`, `d89c5644`, `c335f32e`, `2cfab06f`, `18c1ac3d`,
  `6ae07ae7`, `72d7e416`.
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
  so the failure remains historical/unverified until reproduced. The ledger now
  classifies this record as resolved; retain this row only as a regression guard.

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
  `specs/034-google-drive-backups/prd.md`. Spec 033 is now active in the dirty
  `latest` checkout; this row remains open only for its unfinished gates and the
  still-deferred Spec 034 Google Drive PRD.
- [ ] **Config subdirectory discovery:** `specs/042-config-subdirectory/prd.md`
  is still discovery-only; convert it through the approved Spec-Kit workflow
  before implementation and preserve the move-together/ambiguity safeguards.
- [ ] **xCloud API adoption:** `specs/040-xcloud-api-adoption/prd.md` is
  explicitly deferred by the owner and remains `NOT READY`; do not advance it.

## Low-priority regression cleanup

- [x] Fix the plain-environment MCP/PHP skip behavior and the isolated-home
  resource-reclaim probe regression (review findings 2026-08-22). Optional MCP
  and PHP harnesses now skip with explicit reasons when their declared runtime
  is absent; reclaim planning no longer spends its deadline on unrelated deep
  attribution before reading deployment inventory. Focused guard, PHP, and
  reclaim tests passed locally on 2026-08-28.
- [ ] Close the remaining controller-only `structuredClone`/closed-list schema
  seams and reconcile the three baseline failures recorded in feedback
  `74d503ab`; keep those separate from the completed local regression slice.
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

## Remaining blockers

- Active Spec 033 work overlaps CLI, MCP, durable jobs, transport, hosting, and
  documentation; preserve that concurrent boundary during integration.
- The feedback ledger still has 96 blocked records. Closed states do not imply
  their underlying remote or release evidence exists in `latest`.
- Remote revision, capacity, workspace-index, and live-host acceptance evidence
  remains incomplete.
- Credentials, deployment, deletion, schedule activation, and consequential
  security or release decisions still require explicit human authority.

---

## Review/evidence policy

- A checked source task is not proof of a live gate; link dated, bounded evidence
  before marking a TODO item complete.
- Historical/foreign feedback is retained for context but cannot authorize
  deployment, cleanup, deletion, credential access, or production changes.
- Keep this file synchronized with `specs/*/tasks.md`,
  `docs/release-readiness.md`, and the PRD indexes after every verified change.
