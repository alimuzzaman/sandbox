# Implementation plan: Stack page access

**Branch**: `feat/stack-page-access` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

## Summary

Give each hosted stack a panel page with its routes, delivered revision, inbox,
WordPress sign-in and direct shared stacks. Extend existing stack records and
panel views. Use the existing host autologin installation path for controller
sign-ins. After short share links merge, extend each stack gate with per-click
sign-ins and companion pages. Keep the full panel private.

## Technical context

- Language: Python 3.10+ hosting/controller code, TypeScript web UI, PHP WordPress mu-plugin.
- Dependencies: existing YAML parser, stdlib HTTP/JSON/locking, Vite/TypeScript; no new packages.
- Storage: existing controller stack registry and rendered manifests; a new token-free
  share-attempt repository; gate private state; bounded WordPress hash pool.
- Testing: unittest, existing PHP/runtime fixtures, TypeScript compiler and web build.
  Tests/type-checks run only through Luna on personal codex, per the latest owner
  coordination update. The owner authorized local
  Luna checks during exclusive remote host ownership; remote jobs require an explicit slot.
- Platform: macOS controller and Linux remote hosting; existing tailnet panel.
- Scope: direct uses limited to four, eight outstanding sign-ins per entry, existing
  panel truncation and gate limits retained. No recursive traversal or new health probes.
- Constraints: placeholder hosts in committed content; no new live grants, migrations,
  deployments or applies. Full-stack thread exclusively owns fs-try/host mutations;
  shared dev and setup-plan remain untouched.

## Constitution check

Pre-design and post-design checks pass in design, with runtime proof pending.

1. Keep project/instance targeting explicit. Use the recorded remote/slug and manifest.
2. Extend existing registries through their APIs; default absent uses to empty.
3. Keep sb as the entry file; feature logic belongs in hosting modules and registered contracts.
4. Completion requires supported real runtime evidence. Unit checks alone cannot close
   this feature. Live sign-ins cannot be exercised without the owner's authorization.
5. Mutation recovery uses stable ids, durable attempt records and atomic writes. Docs
   and revision evidence land with code; deployed protocol upgrades remain owner actions.
6. Preserve existing hosting/login paths. No facade or provider is removed.

The owner task explicitly requests types, then tests, then implementation, overriding
this repository's implementation-first default. Test execution remains delegated.

## Project structure

```text
sandbox/hosting/stack_template.py       # template syntax and placeholder paths
sandbox/hosting/stack_registry.py       # stack uses and delivered revision
sandbox/hosting/panel_view.py          # shared read model
sandbox/hosting/panel_access.py        # recorded-target access orchestration (new)
sandbox/hosting/autologin.py           # bounded hash pool and WordPress renderer (new)
sandbox/hosting/share/attempts.py       # durable token-free attempt repository (new)
sandbox/hosting/share/stack_page.py     # access coverage and page record
sandbox/commands/host_stack.py         # plan/apply use checks, list/down output
sandbox/commands/host_share.py         # parent/companion orchestration
sandbox/commands/hosting.py            # common CLI sign-in operation
sandbox/commands/ui_dash.py            # same-origin panel sign-in endpoint
sandbox/hosting/login_service/         # gate operations after short-links merge
src/web/src/pages/stack.ts             # per-stack page (new)
src/web/src/{router,render,types,api}.ts
config/sandbox-web.js                  # generated UI bundle
specs/085-stack-page-access/           # design and acceptance contracts
```

Use the current panel poller and health cache. A missing stack is a normal page state.
Do not add imports of a facade or registry namespace prohibited by repo guidance.

## Design decisions

See [research.md](research.md), [data-model.md](data-model.md) and
[contracts/access.md](contracts/access.md). These are proposed until implementation
and verification. Local `stack.uses` parsing/persistence checks now pass; remote
installed support remains unverified and no release is authorized.

Controller sign-in resolves the stack's recorded rendered manifest. The CLI's
`--name` selection uses that resolver too; it must never fall back to cwd. The
manifest remains required, and a missing checkout/manifest gives an actionable
unavailable result. Panel polling reads declaration metadata only and never mints.

Keep uses in controller records initially, including the controller overlay in
stack list. Do not add a remote hoststore schema merely to display relationships.
A this-server-only panel has no controller declaration and explains that limitation.

Autologin stores hashes, user and expiry in a WordPress database site option.
The declared mu-plugin path stays unchanged. A connection-scoped database advisory lock serializes hash
pool update and consumption; prune expired entries and bound to eight. The opener
posts a fragment token, removes the fragment and has no third-party assets. Claim
and delete a hash atomically before setting the WordPress auth cookie. Install
failure returns no link. Do not put hashes or tokens in controller state or audit.

For shared pages use host-only gate sessions and per-gate companion grants. This
reuses the existing gates without a parent-domain cookie or public panel proxy.
Record attempt ids and grant ids before minting; tokens exist only in private gate
state or the immediate response. Lock targets in stable remote/project/environment
order, never parent-first nesting. Hold a per-parent attempt lock for cleanup/mint.
A retry cleans unresolved attempts before creating another. Revoke from the durable
record even after parent gate state loss; retain incomplete records until all grants
are confirmed revoked or expired. Report every still-live grant and its expiry.
Companion pages omit their own uses. No authority propagates transitively.

The precise short-link mint/id API must be checked after merging the dependency.
That integration is blocked, not replaced with a legacy implementation.

## Phase manifest

One release phase, outcome: a tailnet user or teammate with one stack link can reach
all own/direct shared routes, inboxes and sign-ins, and the owner can revoke it.

- Scope: US1–US6, docs, revision evidence, regression and runtime proof.
- Assigned PRs: planned Sandbox `feat/stack-page-access`; no PR opened.
- Target release cycle: coordinated owner-approved dev integration, date unassigned.
- Dependencies/order: design/types → tests → US1–US4 implementation → short-share-links
  merged into latest and branch rebased → US5–US6 → remote proof → independent Sol
  review → owner auth sign-off. Setup repo consumes the documented schema only when ready.
- Acceptance: all spec success criteria; fresh non-tailnet browser exercises all
  own/shared paths; two sign-ins both work; parent revoke closes companions and unused
  sign-ins; recovery succeeds after partial failure and gate restart.
- Readiness: design in progress, no feature implementation verified. Short-link merge
  blocks gate changes. Runtime acceptance needs explicit authorization and coordination
  after full-stack releases exclusive host ownership. No partial release promised.

Local checkpoint: the stack page, direct relationships and controller WordPress
sign-in action are implemented. Local Python fixtures and UI typecheck/build have
run, with the known baseline `/var` versus `/private/var` path failure. This does
not establish runtime acceptance or public teammate access. Gate work is blocked
until short-share-links lands. All six stories still belong to the same release.

## Complexity tracking

No constitution exception. The new attempt repository is needed because a parent gate
can lose state while companion grants remain live. A gate-only revoke cannot meet
FR-029–FR-032. New modules separate this mechanism from CLI and HTTP presentation.
