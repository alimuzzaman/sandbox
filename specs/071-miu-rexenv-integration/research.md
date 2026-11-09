# Research: Miu Management and RexEnv Integration

Date: 2026-11-08. Decisions below distinguish approved design from facts still requiring release qualification. Inputs include selected architecture and saved read-only upstream reports under `/Users/alim/Sites/git/.scratch/sandbox/miu-rexenv-071/`.

## Decisions

### Miu service API and durable requests

- **Decision**: Extend Miu through its authenticated existing HTTP listener with independent bearer scopes; provision local credentials through a local CLI that accepts secrets only on stdin. Use Node built-in SQLite (minimum Node 22.13) for atomic request identity and run-capacity reservation. API/store behavior follows the selected architecture: idempotent same-intent lookup, conflict for identity reuse with changed intent, one reserved run slot, durable unknown/cancellation fences, finite 10,000-request retention, no stale-fence takeover. Do not expose secrets or private Miu files through Sandbox.
- **Rationale**: Current upstream has no service credential, health/provenance API, durable submit idempotency, safe cancellation fence or scoped App-only enforcement; its GitHub token flow can fall back to env, saved host token or `gh auth`. Existing dashboard authentication is not a service API. A supported upstream contract avoids browser sessions and private state access.
- **Alternatives considered**: SSH/root orchestration into private Miu files, scraping dashboard sessions, stdout credential issuance, and best-effort request retries are rejected because they violate scoped auth or create ambiguous duplicate effects.
- **Evidence/limit**: `miu-management.txt` and `how-miu-management.txt` document source inspection, not live behavior. API implementation, security review and upstream PR acceptance remain blockers.

### GitHub identity, consent and trust

- **Decision**: App creation/adoption remains a human-consented supported Miu flow. Sandbox never receives setup codes or private keys. Run scope is explicitly repository-allowlisted and binds the exact base/head revisions. App installation identity is the only managed GitHub identity; no env, saved token, host `gh`, or personal-token fallback. Report-only is default; each external write requires separate explicit authorization. Execute PR code only under explicit owner-approved trust; shared-host service accounts are not containment.
- **Rationale**: Source inspection found fallback identity paths and no durable idempotency on `/api/start`; current one-slot operation can retry into another run. Existing agent account separation is not independently qualified isolation.
- **Alternatives considered**: Automatic App creation, credentials inferred from host, a network-denylist-as-isolation claim, or widening repository/revision grants are rejected.
- **Evidence/limit**: Design selected; no App was created and no live run or write was performed. Child-run policy propagation and exact upstream behavior need implementation and tests.

### RexEnv pin, runtime and qualification

- **Decision**: Treat RexEnv v0.9.7 at `2e92c00bd927825f1bb818a0bd47a851a0dae27b` as the candidate pin, not master `38cb0d7`. Require exact installed provenance and platform qualification before advertising support. Initial target is Ubuntu 24.04 headless only. Integrate through a qualified native bridge and explicit RexEnv selection; Docker/Caddy remains default.
- **Rationale**: Saved upstream research found v0.9.7 Linux support claims and Linux headless interfaces, but no v0.9.7 Ubuntu 24.04 run; older 24.04 runs used older releases. macOS/Windows headless are refused upstream. No supported generic-exec surface is evidenced.
- **Alternatives considered**: Pinning moving master, inferring qualification from Ubuntu floor, or inventing a generic `exec` invocation are rejected.
- **Evidence/limit**: Read-only report `rexenv.txt`; no installation, execution, or live qualification. Candidate pin needs provenance confirmation.

### Full baseline and restoration

- **Decision**: Define the baseline as a Sandbox-owned stable restore point with positive site ownership and exact site binding, lease/operation identity, supported upstream database export/import, and capture/restore of Sandbox-owned site files. Retain the restore point after restoration. Unknown/incomplete capture, missing ownership, conflicting file state, or uncertain restore outcome refuses completion and retains the fence. Never edit RexEnv private state or claim DB-only coverage as full.
- **Rationale**: Spec requires recoverable database and file state. Saved reports do not establish a usable end-to-end snapshot mechanism or safe upstream arguments, so no CLI syntax is invented.
- **Alternatives considered**: Database-only snapshots, direct edits to RexEnv private directories/database, live push/rollback, or undocumented filesystem copying are rejected.
- **Evidence/limit**: Mechanism is a gating research/implementation item. Before claiming support, identify supported export/import and file ownership APIs on the pinned build, then demonstrate crash recovery on disposable hosts.

### Bounded operations and resource governance

- **Decision**: Reuse Sandbox shared job, secrets, resource, recovery, ingress and resolver services via explicit contracts/manifests. Every mutating request has one stable operation identity, finite budgets, exact target attribution, and truthful partial/unknown outcome. Preserve leases/capacity until completion and cleanup are positively established. Host service proposals enumerate affected and preserved resources and require consent.
- **Rationale**: This matches existing project ownership and prevents timeout/retry from duplicating site/service/QA work or stopping global/unowned resources.
- **Alternatives considered**: Direct registry/state JSON access, prohibited compatibility facades, PID-based stale-lock takeover, or unconditional retry are rejected.
- **Evidence/limit**: Narrow Sandbox interface mapping and contract fit are implementation research; no live runtime facts are inferred.

## Resolved design choices and open release gates

No product-choice clarification remains. The exact supported snapshot/export/import and owned-file capture mechanism, exact adapter/module contracts, pinned Miu release/build provenance, and verified Ubuntu 24.04 behavior are release gates, not unanswered user choices. Resolve with source-backed contracts and approved disposable-host qualification. If supported snapshot capability cannot be established, report the precise gap and keep first-class readiness blocked; do not fabricate command arguments or downgrade the required outcome.


## Implementation evidence update — 2026-11-09

The authorized Miu fork now has upstream draft https://github.com/akashmdiu/miu/pull/1 at `77d67bcc90e1e88d86161fe4dc9ef48906229708`. Its management API, durable admission, worker supervision and App-only policy are implemented, with 92 management tests passing on Node 24.18.0 and Node 26.5.0 on macOS. Independent source reviews found and drove fixes for dropped installed-worker locators, replay after authentication-mode changes, and unchecked cleanup fallback. The last cleanup fix still awaits independent current-head review. No installed Linux service or actual provider behavior was exercised.

The snapshot bridge map additionally identifies supported CLI site metadata and database export/import. It confirms no full local files-plus-database snapshot, generic argv exec or atomic import rollback contract. Snapshot capture therefore needs a separate reviewed owned-files bridge and a demonstrated quiescence mechanism. This research narrows implementation choices but does not qualify that mechanism. Candidate RexEnv v0.9.7 source parity remains mandatory.

The first `setup-plan.sh` invocation selected the persisted older feature 070; it reported an existing plan and copied nothing. The supported explicit `SPECIFY_FEATURE_DIRECTORY=specs/071-miu-rexenv-integration` selector resolved 071. No feature-070 file was modified.


### Exact candidate source check

Fetched candidate commit `2e92c00bd927825f1bb818a0bd47a851a0dae27b` into the existing scratch research clone with `git fetch --no-tags`; the checkout stayed on master and no code ran. At that exact commit, `cli/src/main.rs:275-276` says per-site stop stops serving the site while RexEnv services remain running. It is not writer quiescence proof. `src-tauri/src/core/database.rs:163-169` states exports contain tables/views/triggers and omit stored routines/events; `:183-208` uses a dump subprocess with unbounded connection and no child ownership contract exposed to the caller. A complete database baseline must either prove those omitted objects are absent or use a supported complete export mechanism. Stopping only serving and assuming every export is a complete full-database baseline would be incorrect.

This confirms T003 is a capability-specific design gate. Full snapshot and restore mutation stays blocked until a supported complete capture and quiescence contract exists. Read-only support, public models and metadata work can proceed, but no first-class readiness follows from them.
