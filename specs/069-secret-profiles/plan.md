# Implementation Plan: Secret Profiles and Create-Only Manager Migration

**Branch**: `069-secret-profiles` (feature directory; work stays on active `latest`) | **Date**: 2026-10-26 | **Spec**: [spec.md](spec.md) | **PRD**: [prd.md](prd.md)

**Input**: Feature specification `specs/069-secret-profiles/spec.md` (FR-001..FR-053, SC-001..), ready PRD, source evidence in `/Users/alim/Sites/git/.scratch/sandbox/secret-profiles/{source-inventory-r1,create-contract-r1,create-uniqueness-r3,api-metadata-revoke-r1}.txt`.

## Summary

Add an opt-in **ownership** layer to the existing secret broker: an explicit, names-only map that assigns entries of already-registered sources to ownership profiles (`office`, `personal`, more allowed), each with one operator-configured Infisical destination. Three capabilities ride on it:

1. **Classify** (credential-free dry-run): counts and names per state; unmapped, undecided, multiply-assigned, duplicate-destination and other-domain names block.
2. **Migrate** (create-only, human-approved, journaled): one profile per run into its destination via a new killable, process-group-isolated worker that holds the derived access token in memory, receives values over its stdin pipe, journals with fsync before every POST, verifies by in-memory equality readback, and best-effort revokes the token.
3. **Scoped run**: `sb secrets run --ownership-profile P --ownership-key NAME... -- argv` injects exactly the selected subset under consumer names from declared origins.

Source cleanup is a separate, dry-run-default, separately approved operation. With no `ownership` block, every existing path, digest and output is byte-identical (FR-051).

## Technical Context

**Language/Version**: Python 3.11+ (stdlib only in the worker; same as `sandbox/secrets/providers/worker.py`).
**Primary Dependencies**: existing `sandbox.secrets` package: `SourceRegistry` (sources.py), `parser`/`formats`, `runner.run_with_secrets`, `process_group.terminate_process_group`, `providers/http.BoundedHTTPTransport`, `credentials/manifest.create_credential_store`, `authority.py`, `audit.py`. No new third-party dependency.
**Storage**: controller-local files under `$SANDBOX_HOME/runtime/secret-ownership/` (0700 dirs, 0600 files): `controller-id`, `approvals/`, `journals/`, `runs/`, `locks/`. Non-secret only. Bootstrap credentials in the native OS credential store (operator-provisioned).
**Testing**: standard-library unittest under `tests/`, subprocess tests through `tests/subprocess_support.synthetic_environment` / `run_test_process`; provider behaviour via an injected fake transport; live provider proof only on a separate disposable Infisical instance (never `secrets.asb.bd`).
**Target Platform**: Linux and macOS controllers (FR-053).
**Project Type**: CLI (`sb secrets ownership ...`, one new `sb secrets run` selector). No MCP surface added.
**Performance Goals**: N/A beyond bounds: per-request timeout from the destination descriptor (default 30 s, max 120 s); run deadline `min(approval_effective_expiry, --timeout-seconds <= 3600)`.
**Constraints**: no value, hash, length, prefix or digest of a value anywhere; no `r1_` keyed revision computed, emitted or persisted on new paths (`writer.opaque_revision`/`load_revision_key` are forbidden imports for `sandbox/secrets/ownership/*`); no secret in argv/env/disk; HTTPS exact origin, no redirects/proxies/retries; no list/search/export/admin scopes; no provider upgrade, schema change, provisioning or backups.
**Scale/Scope**: tens to low hundreds of names per profile; 1 profile per run; 1 controller owns each journal.

No NEEDS CLARIFICATION remain; residual protocol uncertainties are fail-closed gates (research R4, R6, R7) proven only on the disposable instance.

## Constitution Check

| Principle | Gate | Status |
|---|---|---|
| I Per-project instance model | Feature is controller-level secret tooling; touches no instance resolution. | PASS (N/A) |
| II Registry single source of truth | No read of `registry.json`; ownership state lives in its own runtime dir; no consumer of `sandbox.registry.COMMANDS`, `sandbox_core.py`, `sandbox.hermes.facade`, MCP `app.py` helpers. | PASS |
| III Single entry, modular package | New package `sandbox/secrets/ownership/`; CLI wired through the existing `secrets` command module (`commands/secrets.py`), no new top-level command registration. | PASS |
| IV Live-stack proof | Definition of done needs CLI acceptance on synthetic sources plus a disposable-instance provider proof; neither is claimed by this plan. Restricted-runtime provisioning approval stays a final human review. | PASS (gated) |
| V Idempotency + docs-with-code | Every operation re-runnable via journal/state; `docs/secret-inspection.md`, `skills/secret-inspection/SKILL.md`, CLAUDE.md gotcha 32 extension land with code. | PASS |
| VI Parity before removal | Nothing removed or disabled; read-only provider path, `useProfiles`, `use-profile`, `migrate-zshrc`, reserved `personal` alias unchanged. | PASS |
| CLAUDE.md module boundaries | Explicit manifest `ownership/manifest.py`; capability checks before side effects. | PASS |
| CLAUDE.md gotcha 32 (Infisical) | Existing ban remains on the spec 067 path. The new ownership create capability requires T044 scoped documentation amendment and the concrete human security review before real use. | CONDITIONAL on T044 and security review |

Post-design re-check (after Phase 1): PASS, no violations; Complexity Tracking empty except the justified new worker below.

## Project Structure

### Documentation (this feature)

```text
specs/069-secret-profiles/
├── prd.md, spec.md, checklists/   (not modified by plan)
├── plan.md            # this file
├── research.md        # Phase 0
├── data-model.md      # Phase 1
├── quickstart.md      # Phase 1
└── contracts/
    ├── config-schema.md         # secrets.ownership grammar + normalization + digests
    ├── cli.md                   # sb secrets ownership ... and run selector grammar/exit codes
    ├── python-interfaces.md     # exact module/class/function signatures (A/B/C seams)
    ├── worker-protocol.md       # parent<->ownership worker framed pipe protocol
    ├── provider-infisical-v4.md # exact HTTP calls, response classification
    ├── storage-evidence.md      # operator-attested evidence document + admission
    └── journal-approval.md      # on-disk journal/approval/run record formats + locking
```

### Source Code (repository root)

```text
sandbox/secrets/ownership/          # NEW package (deep, small modules)
├── __init__.py        # re-exports public names only
├── models.py          # [A] frozen dataclasses/enums/error codes (contract first)
├── config.py          # [A] normalize_ownership(); called by config/secrets.py
├── classify.py        # [A] classify(); pure, credential-free
├── continuity.py      # [A] SourceContinuity capture/compare (stat-based, no values)
├── source_view.py     # [A] names-only and bounded value reads via SourceRegistry
├── manifest.py        # [B] OWNERSHIP_MANIFEST + load_ownership_adapter()
├── protocol.py        # [B] frame encode/decode shared by broker and worker
├── worker.py          # [B] stdlib-only child: login, metadata, create, readback, read, revoke
├── broker.py          # [B] OwnershipBroker: spawns worker, process-group lifecycle
├── evidence.py        # [B] storage evidence parse + admission
├── journal.py         # [C] Journal (flock + fsync), state machine
├── approval.py        # [C] ApprovalStore, human-only grant, binding checks
├── migrate.py         # [C] run_migration() orchestrator
├── cleanup.py         # [C] run_cleanup()
└── scoped_run.py      # [C] resolve_scoped_bindings() for `secrets run`

Edited (single owner each):
sandbox/config/secrets.py          [A] accept optional "ownership" key; call normalize_ownership
sandbox/secrets/authority.py       [A] ownership_digest(); optional ownership consumers in _fingerprint
sandbox/secrets/context.py         [A] pass normalized ownership into SecretService (unused when absent)
sandbox/secrets/credentials/base.py [B] locator_for_ownership()
sandbox/secrets/writer.py          [C] remove_keys_create_free() (no opaque_revision)
sandbox/commands/secrets.py        [C] "ownership" action + run selector flags
sandbox/secrets/audit.py           [C] new operation names in allowed list (schema_version stays 1)
docs/secret-inspection.md, skills/secret-inspection/SKILL.md, CLAUDE.md gotcha 32   [C] docs-with-code

tests/ (written after all implementation, see quickstart):
test_secret_ownership_cli_acceptance.py, test_secret_ownership_config.py,
test_secret_ownership_classify.py, test_secret_ownership_continuity.py,
test_secret_ownership_worker.py, test_secret_ownership_journal.py,
test_secret_ownership_approval.py, test_secret_ownership_migrate.py,
test_secret_ownership_cleanup.py, test_secret_ownership_scoped_run.py,
test_secret_ownership_boundaries.py (AST/import guards)
```

**Structure Decision**: one new package with three disjoint implementer ownerships. **A** (config/model/classification/continuity) lands `models.py` first and freezes it; **B** (provider adapter, worker, broker, evidence, locator) and **C** (journal, approval, orchestration, CLI, writer, docs) code against `contracts/python-interfaces.md` and never edit each other's files. Integration order: A → B ∥ C → integration → formal tests.

## Implementation Phases

| Phase | Owner | Outcome | Exit check (run later by Haiku via run_test_process) |
|---|---|---|---|
| P0 | A | `models.py` frozen exactly as contracts | import + dataclass field equality vs contract |
| P1 | A | config, digests, classify, continuity, source_view | `sb secrets ownership classify` on synthetic config; old digests unchanged |
| P2 | B | manifest, protocol, worker, broker, evidence, locator | fake-transport worker round trips |
| P3 | C | journal, approval, migrate, cleanup, scoped run, CLI, writer, docs | CLI dry-run/approve refusal paths |
| P4 | all | integration + formal tests (CLI synthetic acceptance first, then focused regressions) | full `tests/test_secret_*` regression |
| P5 | human | disposable Infisical instance proof (R4/R6/R7), security review, restricted-runtime provisioning approval | not claimed here |

Release fields: one coherent phase (feature 069); no partial publication; no tag/release/deploy authority granted by this plan.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Second worker module (`ownership/worker.py`) beside `providers/worker.py` | Migration needs an interactive journaled create loop and a distinct capability; FR-038 forbids widening the read-only worker | Adding actions to the read-only worker widens its accepted request schema (`{"version":1,...}` exact-key check) and the read-only authority |

## Parent integration refinements

- Initial source-backed ownership entries use registered dotenv files; normal
  existing source support is unchanged. A adds a generic descriptor-safe
  SourceRegistry.read_with_stat snapshot, with no ownership import in sources.py.
- Optional gateway mode supports the already-approved Cloudflare Access machine
  boundary using a protected native-store bundle; no portal bypass or Cloudflare
  administration. All HTTP requests carry an honest fixed client identifier.
- Authority fingerprints bind the complete ownership map, including domain,
  destination, origin and gateway; old fingerprints remain unchanged with no opt-in.
- Provider input losslessness is checked for the entire batch before the first
  create, including the exact JavaScript trimming boundary set.
- Controller-qualified approval/run IDs make foreign-controller guidance possible
  without migrating journals or inventing a distributed registry.
- Protocol-source gaps must be resolved before provider implementation is called
  complete; V2_SELECTION_QUERY must not remain an unconditional refusal placeholder.
