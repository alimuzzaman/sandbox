# Implementation Plan: Permanent hosted WordPress login

**Branch**: `feat/host-autologin-permanent-links` | **Date**: 2026-11-12 | **Spec**: [spec.md](spec.md)

## Summary

Add an explicit `host login-url --permanent` path for native hosted WordPress. Preserve the ordinary renderer and local login sources unchanged. Permanent links use a fragment-to-POST bridge with the same-origin referrer policy proven on the development site.

## Technical Context

**Language/Version**: Python 3.12; generated WordPress PHP.
**Primary Dependencies**: Existing standard library, CLI parser, WordPress APIs; no new production dependencies.
**Storage**: Existing installed helper stores one credential hash; host metadata contains user, zero expiry and one_time=false only for permanent mode.
**Testing**: Python unittest, generated PHP behavior, real supported CLI and isolated WordPress/browser acceptance where available.
**Target Platform**: Existing hosted Compose WordPress; local tokens and login_service/share modes unchanged.
**Performance Goals**: No changes to normal path rendering or calls. Permanent login does not add use-counter writes.
**Constraints**: No plaintext permanent key in request targets or retained state; preserve ownership/confirmation and exact same-origin checks.
**Scale/Scope**: One optional policy for one existing command.

## Constitution Check

- I/II: Existing explicit project/target resolution and owner locks stay authoritative. No direct registry/state reads.
- III: CLI option belongs to existing parser/handler; pure rendering stays in existing core hosting owner.
- IV: Actual CLI and isolated runtime evidence required; unavailable proof must remain explicitly open.
- V: Issuing replaces the credential by existing semantics; never replay unknown issuance blindly. Docs and checks ship together.
- VI: The default renderer, local login code and other providers are retained. No old behavior removed.
- Human security review remains required before merge/release. No deployment is part of this request.

## Project Structure

- `sandbox/cli.py`: opt-in flag, mutually exclusive with explicit TTL.
- `sandbox/commands/hosting.py`: scope checks and mode selection inside existing owner transaction.
- `sandbox/core/_hosting.py`: separate permanent renderer and backward-compatible URL helper.
- `tests/test_host_autologin_permanent.py`: focused Python/PHP compatibility and refusal coverage.
- `docs/remote-hosting.md`, `README.md`: usage and risks.

## Design and interfaces

- Retain `render_autologin_mu_plugin(hash, user, expires_at)` byte-for-byte.
- Add `render_permanent_autologin_mu_plugin(hash, user, origin) -> str`.
- Add keyword-only `permanent=False` to issuer and URL rendering; existing calls retain behavior.
- Permanent URL uses a public marker and `#sandbox_autologin=<key>`. GET emits a restricted bridge; HEAD does not authenticate; POST requires exact HTTPS origin and key hash, then resolves the configured user.
- Refuse permanent requests for unsupported provider/actions or combined TTL/use/list/revoke/email selectors before installation/state mutation.

## Phase manifest

- Outcome: explicit reusable hosted WordPress access without changing existing login behavior.
- Scope: all source, docs and regressions above; one coherent feature.
- Assigned PRs: planned Sandbox feature PR, not opened.
- Target release cycle: unassigned; no release authorization.
- Dependencies/order: none beyond existing deployed WordPress integration.
- Acceptance: FR-001..FR-008 and SC-001..SC-004 in spec; source review and runtime evidence.
- Readiness/blockers: source/docs and 298 focused checks complete; full-suite acceptance is incomplete, with five observed baseline failures/errors; live WordPress acceptance is blocked by the unavailable local Docker daemon. No deployment, release or verified handoff.

## Complexity Tracking

No new service, registry, provider or persistence layer. New permanent renderer isolates its new transport from the unchanged default.
