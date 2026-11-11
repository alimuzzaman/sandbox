# Implementation Plan: Managed server installation

**Branch**: plan/shared-server-installation | **Date**: 2026-11-11
**Spec**: spec.md | **Input**: reviewed PRD and clarified specification

## Summary

Add explicit lifecycle binding in owner and managed layouts first, then protected
managed release/install planning and journaled migration/recovery adapters. Keep
joined-controller repair independently committable. All live changes remain root's
serialized operations; no rollout or destructive migration is part of source work.

## Technical Context

Python >=3.12 standard library, existing argparse CommandSpec manifests, Starlette
control service, Linux systemd. Filesystem identity/policy and durable JSON journal;
existing SQLite/recovery repositories stay behind their owning adapters. No new
external package. Short synthetic helper unittest checks, <=60s per command.
Targets: existing Linux owner install and opt-in managed Linux host. Local/macOS
personal paths unchanged. No broad tests/builds/soak in this session.

## Constitution Check

I/II: project-instance ownership and authoritative registries preserved, no new
implicit instance. III: single sb entry retained; new installation package and
manifest-owned command. IV: source readiness explicitly distinct from runtime
acceptance; disposable/live proof remains required before release/cutover. V:
request-bound idempotency and docs/tests with code. VI: no removed legacy path,
managed opt-in only. No justified exception or compatibility facade removal.
Recheck after design: unchanged; no downstream runtime claim without evidence.

## Project Structure

- sandbox/installation/{binding,paths,lifecycle,migration,extensions}.py
- sandbox/commands/installation.py registered via commands/manifest.py
- mcp/wp-server/server.py authenticated discovery composition only
- scripts/install-remote.sh and supported service lifecycle initialization
- config/systemd/sandbox-managed.service, docs/managed-installation.md
- tests/test_installation_*.py, focused fixture-only checks
- specs/089-managed-server-installation/{research,data-model,quickstart}.md
- specs/089-managed-server-installation/contracts/installation.md

## Design and phases

P1 genuine binding: private owner-checked identity and policy persisted only by
explicit lifecycle initialization. Reads validate types/owner/mode/no symlink,
canonical machine identity and service marker; runtime revision from actual source.
validate_current compares every authority field including policy. No secret values.
Capabilities from installed extension composition, default empty, not stored claims.

P2 managed lifecycle: immutable release descriptor and approved paths, account/path
collision and release validation, generated system unit with explicit state/home,
protected policy, fixed identity. Explicit plan/apply wrappers invoke narrow injected
host adapter operations; no arbitrary shell or client-selected privileged paths.
Owner-mode initialization also wired through supported lifecycle so086 need not
wait for relocation. Release and service authority changes are request-bound.

P3 migration: journaled phases and prerequisite manifests. Supported adapters own
inventory, encrypted backup/restore proof, drain/fence, copy/verify/schema locators,
service switch and rollback. Unknown prerequisites refuse; never fake completion
for missing adapters. Keep source and original evidence. Writes after activation
require forward repair/reverse migration, never stale rollback.

## Validation and release

Focused source tests use synthetic environments and private temporary directories,
never parent environment copies. Include malicious path/symlink, malformed binding,
policy/machine drift, stable retries, capability truth, release collision, recovery
proof gaps, crash/resume and post-write rollback refusal. Parent never runs tests.
Root controls exact candidate compatibility assessment and any real lifecycle call.
Live acceptance matrix and commands in quickstart/docs are concrete future actions,
not executed checks. Independent personal Sol source review before handoff.
