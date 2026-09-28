# Provenance and traceability inventory

Read-only source inventory captured from Sandbox `latest` at
`fd7d650f8bfbb1760d931e2484cc01fa0badf546` (2026-09-08). This report maps the
durable stores and public projections that can explain an activation, job, host
apply, release, or local instance. It does not claim that any remote, edge, or
production state was observed.

## Finding

There is no single end-to-end provenance record. Evidence is split across:

1. Lenzora's private per-target deployment run directory.
2. Sandbox's image staging ledger and nested activation state in `hosts.json`.
3. Sandbox's durable job SQLite database.
4. Sandbox's ordinary hosted target record and remote `apply.log`.
5. Sandbox's local instance registry and generated per-instance files.

The strongest join is the exact source SHA plus the deployment's request IDs,
proof or receipt digests, generation, and job IDs. A successful current state
does not by itself prove the full historical path that produced it; most stores
retain a bounded current state or bounded recent history.

## Store map

| Question | Authority / path | Useful fields | Limits |
|---|---|---|---|
| Which Lenzora release was selected? | Lenzora private run directory, `release.json` | target, revision, branch, GitHub workflow `runId`/`runAttempt`, artifact ID/name | Private local retention; selection is one run record. [`release.ts`](/Users/alim/Sites/git/lenzora/scripts/hosted-deployment/release.ts:57) |
| Was the downloaded image bundle verified? | Same run directory, extracted bundle and `preparation-evidence.json` | receipt `source_sha`/target, workflow evidence, bundle `receiptDigest` | Bundle and receipt are retained privately; this is release/image evidence, not running-host proof. [`artifact.ts`](/Users/alim/Sites/git/lenzora/scripts/hosted-deployment/artifact.ts:57) |
| What image was staged on the host? | Sandbox Feature 050 ledger under `$SANDBOX_HOME/runtime/hosting/image-staging/ledgers/` | stage request/digest, staging generation, proof digest, ledger revision, result, proof leases/tombstones | Per-target bounded ledger; no general deployment timeline. `StageRepository` derives one ledger filename from target identity. [`staging_repository.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/hosting/images/staging_repository.py:197) |
| What activation is current or in flight? | Shared `$SANDBOX_HOME/runtime/hosts.json`, host record `image_activation` | generation, current/previous generation digests, active request/transaction/phase/effect flag, terminal results, recovery results, tombstones | Retains current plus one previous generation and bounded result maps; public status intentionally projects only a closed summary. [`status.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/hosting/images/activation/status.py:6) |
| What did image activation prove? | `hosts.json` nested activation generation and v2 transaction | plan/proof/policy digests, target identities, compose snapshot, replacement intent, service/image/runtime projections, running observation, generation subject, edge receipt | Secret-free closed values. Raw Compose/env/command data is intentionally absent. Recovery is only observation authority. [`v2_repository.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/hosting/images/activation/v2_repository.py:1) |
| What ordinary hosted apply is current? | Shared `hosts.json`, target record keyed by registered remote/project/environment | requested, staged, recorded and observed revisions; source identity/clean flag; config/manifest digests; runtime/topology/health; edge; generation; latest recovery | The durable `hosting_operation` is overwritten by the next apply and is absent for non-durable callers. Only the latest recovery is projected by status. [`hosting.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/commands/hosting.py:2243) |
| What source/runtime evidence did ordinary apply capture? | Target record `hosting_operation` while a durable apply is active or retained | job/request IDs, target, accepted timestamp, source commit/identity/branch, host/machine identity, config and manifest digests, topology, image rows, phase receipts | It is a latest-operation receipt, not append-only deployment history. It is created only when the durable job context is present. [`hosting.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/commands/hosting.py:1801) |
| What happened in an ordinary failed apply/recovery? | Target record `recovery_attempts` and `recovery_tombstones` | original job/request, recovery request/digest, result family/class, effect scope, expected/resulting generation, evidence ID/expiry, phase states, accepted/started/completed timestamps | Up to 64 live attempts, then bounded tombstones. `host status` exposes only the latest recovery summary; inspect state through the supported command. [`repository.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/hosting/recovery/repository.py:287) |
| What durable jobs ran? | `$SANDBOX_HOME/runtime/jobs/registry.sqlite3` | job/request IDs, parent/root/retry, project/target/remote/workspace, lifecycle/health, source commit/dirty digest, policy provenance, accepted/queued/started/finished/updated timestamps, exit/termination, result, cleanup/integrity | Jobs are queryable and ordered by acceptance time. Job events, output streams, heartbeats, metrics, process identity and artifacts are separate tables. [`registry.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/jobs/registry.py:220) |
| Which exact tree backed a remote workspace job? | Remote owner-only receipt under `$SANDBOX_HOME/runtime/workspaces/deployment-receipts/` | receipt ID, project identity, checkout/source checkout locator, source identity, commit, dirty overlay digest | This receipt is created by remote workspace deployment and the public job snapshot carries only its opaque ID; it is not a release or host activation receipt. [`_remote.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/core/_remote.py:480) |
| What local instance exists? | `$SANDBOX_HOME/runtime/registry.json` plus project `sandbox.local.yml` | canonical project root + label, instance name/incarnation, status, ports, server, URL/admin/login URLs, PHP/WP versions, source/config identity | Registry is an instance catalog, not a build/deploy receipt. The final `ensure_instance` record has no source Git SHA, image digest, creation/completion time, or health receipt. [`_instances.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/core/_instances.py:1176) |
| What generic Compose runtime was observed? | Generic runtime registry plus live adapter status | instance/root/label/service/port/URL/status/lifecycle, Compose `ps` output, observation source/freshness | Generic adapter stores no source revision, image digest, or durable observation time. [`compose.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/runtimes/compose.py:326) |
| What remote apply log exists? | Remote runtime host path `<SANDBOX_HOME>/runtime/hosts/<project>/<environment>/apply.log` | bounded sanitized Caddy validation/reload/observation phase and digest lines; path returned by diagnose | It is remote side evidence, not read by the normal status projection and not a job ledger. [`hosting.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/commands/hosting.py:2664) |

## Activation and release join

The Lenzora deployment frontend creates a deterministic run identity from the
release selection, bundle receipt digest, and starting generation. It retains
`run.json`, then attaches separate `prepare-job.json` and `activate-job.json`
records. The run binds `requestId`, `stageRequestId`, and `activationRequestId`.
[`state.ts`](/Users/alim/Sites/git/lenzora/scripts/hosted-deployment/state.ts:17)

The normal sequence is:

`release.json` → extracted signed receipt and preparation evidence → `plan.json`
→ staged proof and `proof.json` → `prepared.json` → Sandbox activation request
→ `terminal.json`.

The preparation path validates that the receipt source SHA equals the selected
release, that workflow evidence matches the selected run, and that the receipt
bytes did not change during verification. [`artifact.ts`](/Users/alim/Sites/git/lenzora/scripts/hosted-deployment/artifact.ts:63)
Lenzora then calls Sandbox `host image authority`, `validate`, data readiness,
policy/plan verification, stage-bundle, `host stage`, and activation-bundle.
[`native.ts`](/Users/alim/Sites/git/lenzora/scripts/hosted-deployment/native.ts:69)

At activation, the public join is:

`release.revision` → receipt `source_sha` → plan-set digest → staged proof digest
→ Sandbox activation request/digest → transaction digest → resulting generation
and generation digest.

This join is strong for the release and activation authority. It has a
cross-system discontinuity at jobs: Lenzora's durable job request runs from the
Sandbox control checkout and binds `controlRevision`, while the release source
SHA is the Lenzora application checkout SHA. The job binding code checks the
control checkout revision, project root, request ID, and worker argv, but does
not store the application release SHA in the job record. [`job.ts`](/Users/alim/Sites/git/lenzora/scripts/hosted-deployment/job.ts:12)

Lenzora's retained terminal record keeps only the successful activation
projection (request ID, transaction digest, generation relation, generation
digest). It does not copy Sandbox job terminal timestamps, health, image IDs, or
the full generation subject into the run directory. [`native.ts`](/Users/alim/Sites/git/lenzora/scripts/hosted-deployment/native.ts:152)

## Ordinary hosted apply join

`./sb host apply` resolves the source commit, captures a bounded dirty overlay,
ensures the remote source checkout, pushes the exact commit, and records
requested/staged source before runtime convergence. The public apply JSON returns the commit, derived-environment projection, target, and optional apply-log/cache-purge fields. Requested/staged/recorded/observed revisions and runtime/edge projections are available through other internal/status records; they are not all copied into the final apply JSON. Root correction verified at `sandbox/commands/hosting.py:5620-5635`. [`deploy.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/commands/deploy.py:217)
[`hosting.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/commands/hosting.py:2960)

For a current-contract durable apply, the job supervisor supplies a job ID and
request ID. Sandbox persists the operation before effects with source identity,
source commit, target, generation, config/manifest digests, topology, and
machine/host identity; later observation fills image, config-file, phase, and
runtime evidence. [`hosting.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/commands/hosting.py:1827)
[`hosting.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/commands/hosting.py:1977)

After success, the target record holds the current runtime/edge receipt and the
latest `hosting_operation`. A later apply replaces that operation. Failed
recovery is append-oriented only within the bounded `recovery_attempts` window;
older rows become tombstones with phase detail removed. [`repository.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/hosting/recovery/repository.py:550)

Use these read-only projections for investigation:

* `./sb host status --project-dir DIR --environment ENV --remote NAME --json`
  for current source, runtime, topology, health, edge, generation, and latest
  recovery summary.
* `./sb host diagnose --project-dir DIR --environment ENV --remote NAME --json`
  for status plus bounded disk/image rows and the remote apply-log path.
* `./sb job-list --project-dir DIR --remote NAME --json`, then
  `./sb job-status JOB_ID --remote NAME --json` and `./sb job-output JOB_ID`
  for durable job lifecycle and retained output.
* `./sb host image status --project-dir DIR --environment ENV --remote NAME
  --json` for retained activation generation, active transaction, and image
  activation terminal result. It does not observe the remote runtime.
* `./sb host logs --project-dir DIR --environment ENV --remote NAME
  --apply-log --lines 1000` for the bounded remote apply log after diagnose
  returns its path.

For durable workspace jobs, resolve the opaque deployment receipt through the
workspace service before interpreting a job's `source_commit` or dirty digest.
The receipt binds the deployed checkout and source checkout to the project
identity, and the job submission snapshot retains the opaque receipt reference;
neither side alone is a complete host deployment proof. [`context.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/application/context.py:1374)

## Local instance traceability

`ensure_instance` uses canonical project root plus label, allocates/reuses an
instance name and ports, writes a pending registry record before boot, generates
Compose, installs WordPress, wires plugins/themes, captures install snapshots,
then writes a ready registry record. [`_instances.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/core/_instances.py:1326)
[`_instances.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/core/_instances.py:1375)

The local registry can prove instance identity, URLs, ports, configured PHP/WP
versions, and ready/pending status. It cannot answer which Git commit or image
digest produced the running containers. Generic Compose status is a fresh
adapter observation, but its registry record still has no source/image
provenance. [`JsonRegistryRepository`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/project_registry/json.py:72)

## Evidence boundaries and gaps

* `hosts.json` is the shared outer authority. The recovery repository is the
  sole parser/writer/locker; image activation is nested and preserves unknown
  sibling fields. [`repository.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/hosting/recovery/repository.py:640)
* Activation has stronger cryptographic and generation binding than ordinary
  local instance records: exact target identity, plan/proof/policy digests,
  runtime observations, and generation-bound edge receipts are retained.
* Ordinary `host status` makes a fresh remote runtime observation when the
  registered remote is provisioned, but its recorded history remains a current
  projection. A healthy status is current evidence, not a complete release
  timeline.
* `host diagnose` image rows include Compose service/name/image/ID/created/size
  fields from the remote observer, but these are an on-demand diagnostic and
  are not copied into an append-only host release history. [`hosting.py`](/Users/alim/.codex/worktrees/24eb/sandbox/sandbox/commands/hosting.py:2697)
* Durable job records include complete timing and source fields, but job
  request identity alone does not link to an application release unless the
  caller stores that release identity in its own run state.
* Lenzora's `deploymentHome` retains numbered target directories and refuses
  ambiguous unfinished runs or excessive history, but retention is local to
  that frontend and has no automatic reconciliation with Sandbox's job or host
  history. [`cli.ts`](/Users/alim/Sites/git/lenzora/scripts/hosted-deployment/cli.ts:23)
* Local fake/unit tests and retained receipts prove codec/state-machine
  behavior only. They do not establish registered-host, live-edge, deployment,
  or production state.

## Minimum evidence packet for a future incident

For one exact target, collect the following bounded, read-only records before
attempting any replay: Lenzora `release.json`, `preparation-evidence.json`,
`receiptDigest`, `plan.json`, `proof.json`, `prepared.json`, `run.json`, both job
attachments and `terminal.json` if present; Sandbox `host status`, `host
diagnose`, `host image status`, exact job status/output, the target host record
under the supported repository projection, staging ledger result/proof
identity, and `host logs --apply-log` output. Join on target, application revision, stage/activation request
IDs, proof/generation digests, and job IDs. Treat any missing, stale, foreign,
or contradictory identity as an evidence gap requiring the fail-closed recovery
path.
