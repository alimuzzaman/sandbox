# Implementation Plan: Shared server secret use

**Branch**: `feat/shared-server-secret-use` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/086-shared-server-secret-use/spec.md`, ready [PRD](prd.md), independent PRD review and specify/clarify report. Baseline supplied by root: `e46777b`. This is planning, not implementation or runtime acceptance.

## Summary

Deliver explicit server credential selection, declaration-only static validation and an early unsupported-capability result. Keep credential-consuming server execution disabled. No local keys are required for server static validation, and server-key availability stays unverified. Personal-local behavior stays available, including existing remote operations that use that explicit/default personal-local scope.

The executable source slice covers FR-001/002/003/014/018/021/024 and disabled portions of the remaining requirements. Future broker binding and admission contracts document FR-004 through FR-020 and FR-022/023 without building a speculative executor, ledger or grant service. Full end-to-end acceptance is BLOCKED on authenticated installed owner-local dispatch/admission and combined integration evidence. A matching context frame, copied source, manifest or passing unit check cannot enable it.

## Technical Context

**Language/Version**: Python 3.12, from `.python-version`; existing shell-to-Python `sb` entry remains unchanged.

**Primary Dependencies**: Existing argparse command registration, hosting declaration parser, immutable Python records and existing bounded result/error conventions. No new dependency, provider adapter, transport or service.

**Storage**: No new persistence for the executable slice. Pure declarations/results only. Future retained admission/refusal belongs in the accepted durable service, never a second ad hoc JSON ledger.

**Testing**: Existing Python unit suite and CLI contract coverage. Later helpers run focused behavior checks and repository-required gates using synthetic subprocess environments. No tests run during planning.

**Target Platform**: Existing macOS/Linux controllers; eventual selected Linux private owner installation. No path, permission or managed-layout migration.

**Project Type**: CLI source groundwork, with proposed integration contracts.

**Performance Goals**: No new latency/throughput promise. Disabled server operations perform zero remote calls, secret accesses, writes, claims or process launches after argument parsing. Bounded existing declaration-file reads are allowed for static validation only.

**Constraints**: No provider values in public objects, transport, argv, logs, polls, artifacts, caches, staged files or retained storage. No owner environment inheritance, dummy presence, alternate-host/local fallback, self-SSH, self-remote or `--here`. Equal admitted owner rights, including currently approved sudo, remain required. Current generic secret-run escalation guards are not weakened.

**Scale/Scope**: One existing host command family; a small pure credential-selection module and bounded command branches. No operational concurrency, reservation or persistence implementation. Inventory is unavailable in this slice. Numerical timeout/retention budgets for future execution are deliberately not invented; integration must use a finite accepted policy and keep lookup/tombstones sufficient to prevent identity reopening.

## Constitution Check

*Pre-research gate and post-design recheck against constitution 1.0.2.*

| Principle | Before research | After design |
| --- | --- | --- |
| I. Per-project model | Preserve registered project targeting; no fallback instance | PASS in design. Static validation never creates an instance; future server refs require registered project context |
| II. Registry authority | No direct state/registry JSON reads | PASS in design. Existing resolution contracts only; declaration syntax does not prove registration |
| III. Entry/modularity | Keep `sb` entry and explicit manifests | PASS in design. Feature helper under `sandbox/hosting/`, host remains registered by existing command manifest |
| IV. Live proof | Live evidence unavailable and prohibited here | BLOCKED for full feature acceptance. Source groundwork can be reviewed separately; no exception or full-done claim |
| V. Idempotency/docs | No source-side mutations in the slice | PASS in design. Repeatable refusal/static validation, matching README and skill docs in eventual source change |
| VI. Parity/removal | Preserve all working personal-local paths | PASS in design. Gate only newly selected server scope; remove no facade, proxy or working path |

No unresolved product clarification blocks the source plan. Installed integration remains an explicit execution gate, not an assumption resolved by paperwork. Do not report the whole constitution gate green. Source implementation may proceed only through root's formal tasks/analyze/review gates and must retain this limitation.

## Project Structure

### Documentation (this feature)

```text
specs/086-shared-server-secret-use/
  plan.md
  research.md
  data-model.md
  quickstart.md
  contracts/source-groundwork.md
  contracts/future-owner-broker.md
```

`tasks.md` is intentionally absent. This plan used the installed speckit-plan workflow and setup-plan template. Root owns subsequent task generation, analysis and agent-context update.

### Source Code (repository root)

```text
sandbox/hosting/credential_selection.py    # proposed new pure selection/static result types
sandbox/commands/hosting.py               # existing cmd_host boundary, validate/secrets branches
sandbox/commands/host_stack.py            # existing stack boundary before _up setup
sandbox/cli.py                           # existing host parser compatibility bridge only
sandbox/commands/manifest.py             # existing host owner registration, retain it
sandbox/registry.py                      # optional typed preconfiguration dispatch hook
sandbox/config/manifest.py               # no new global/project schema provider needed
README.md                                # scope, disabled behavior, revision evidence
skills/secret-inspection/SKILL.md         # scope/custody and unavailable inventory guidance
tests/                                   # focused credential selection and command behavior checks
```

Test filenames are to be chosen in tasks alongside existing host/stack tests; no new test framework. Public options must remain owned by the registered host command contract. If adding flags requires touching the parser bridge, keep it bounded rather than migrating the entire parser. No new consumer of compatibility facades or `sandbox.registry.COMMANDS`.

**Structure Decision**: Add a pure helper used by the real command boundary, rather than unused distributed abstractions. Do not add fields to machine configuration, migrate secret sources, or extend the stack-template schema in this slice. Nonsecret CLI references suffice. The source helper has no secret-reader/process/network/storage dependencies.

## Phase 0: Research outcome

[research.md](research.md) records source evidence, decisions and rejected approaches. All source-design unknowns resolve to the explicit disabled slice. Runtime API availability, wire acceptance and canonical installation provisioning remain dependencies that block enablement; they are not silently supplied by local observations.

## Phase 1: Design and implementation boundaries

1. Parse a discriminated personal-local/server selection using [source contract](contracts/source-groundwork.md). Reject malformed, mixed or unsupported selections with bounded fixed diagnostics. User-supplied actor/uid/home/value fields are never authority.
2. Add an optional typed `CommandSpec` preconfiguration dispatch hook in `sandbox/registry.py`, registered by the existing host owner. Invoke it in `sandbox/cli.py` immediately after host argument normalization and before auto-migration, `load_config`, instance/registry resolution or compatibility writers. A handled server request returns immediately; personal-local declines and preserves startup. Merely extending `predispatch_policy` is insufficient because it still loads configuration. Cover malformed/mixed selections, static validate, unavailable inventory and every server action through this hook. Retain defenses at `cmd_host` and the stack public boundary: branch on server selection before resolving local keys, remote entries, lifetime stores, target leases, source exports/worktrees, manifests or durable jobs. Direct handler entry must not bypass the same guard. `host validate` takes a dedicated nonsecret path; other server-scoped actions refuse except unavailable inventory. `stack up --plan` also refuses because its existing plan path is not static-only.
3. Static validation checks declaration shape and exact required/generated mapping names through reusable pure parsing, without calling `_secret_status`, provider clients or existing effectful plan helpers. If existing validation combines pure and effectful work, extract only the smallest pure declaration check required; do not duplicate a full manifest validator. Inline provider `secrets.values` is unsupported in server declarations and must produce a fixed non-echoing error. Never resolve/check local provider presence to validate server scope.
4. Return unverified server availability after valid static checks. Refuse execution with a stable code and no admission, request reservation or grant. Supported inventory is not implemented; server inventory returns unavailable without inspecting source metadata. No enablement flag or injectable production capability=true path.
5. Document nonsecret complete binding requirements in [future contract](contracts/future-owner-broker.md) and [data model](data-model.md). Implement only declaration/result records required by current branches. Future admitted-operation/grant/ledger descriptions remain documents; do not build generic unused state machinery or mock it as accepted integration.
6. Finish source changes and docs before exercising supported static/refusal CLI behavior. Only then add focused observed-behavior regressions and have authorized helpers run gates. Record local/runtime revisions appropriately when integration is authorized; this planning shard makes no installed revision assertion.

## Validation coverage and release boundary

[quickstart.md](quickstart.md) separates later executable source checks from blocked integration cases. Required source coverage includes whole-public-CLI traps for attempted auto-migration, configuration/registry/instance reads and compatibility writers, alongside handler-level defenses. Isolated home alone is insufficient proof. Required source coverage also includes clean laptop, inaccessible secret readers, early refusal before every legacy effect entry, malformed/wrong-server declarations, unsupported inventory, fixed diagnostic output, provider/generated separation, and personal-local compatibility. Binding mutation, replay concurrency, real provider failures and admitted sudo consumer tests require installed integration and are deferred explicitly.

No source test may instantiate fake committed admission and then call production effects. Test doubles may prove that forbidden boundaries were never called on disabled paths. Captured test children use `tests.subprocess_support.run_test_process` or `synthetic_environment` with fixed synthetic overrides; never copy/enumerate/unpack/pass parent `os.environ`.

Root reservation resolves numbering to 086 for this feature and 085 for sibling hosted-site-baselines; no sibling edits are required. Before integration root must agree the proposed wire, establish canonical installation identity beyond machine/uid/home, and independently evidence every FR-023 prerequisite. Before release, flag authentication, authorization, custody and production-path changes for human review. Runtime acceptance needs separate operational authorization.

## Complexity Tracking

| Violation or limitation | Why recorded | Simpler alternative |
| --- | --- | --- |
| Principle IV full acceptance remains blocked | Installed authenticated dispatch/admission is unavailable; source evidence cannot prove server behavior | Keep server capability disabled and report source outcome separately. Do not waive live proof |
| Agent-context update omitted in this shard | User ownership expressly excludes metadata/context/gate/link edits | Root may perform the installed context update later within its scope |
