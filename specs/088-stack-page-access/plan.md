# Implementation plan: Stack page access

**Branch**: `feat/stack-page-access` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Numbering provenance**: Renumbered from `085-stack-page-access` to
`088-stack-page-access` on 2026-11-11 under the coordinator reservation. Existing
readiness reviews and historical evidence retain their original 085 identity;
the accepted requirements and review evidence are unchanged.

## Summary

Give each hosted stack a panel page with its routes, delivered revision, inbox,
WordPress sign-in and direct shared stacks. Extend existing stack records and
panel views. Use the existing host autologin installation path for controller
sign-ins. With the short share links dependency merged locally, extend each stack gate with per-click
sign-ins and companion pages. Keep the full panel private.

## Technical context

- Language: Python 3.10+ hosting/controller code, TypeScript web UI, PHP WordPress mu-plugin.
- Dependencies: existing YAML parser, stdlib HTTP/JSON/locking, Vite/TypeScript; no new packages.
- Storage: existing controller stack registry and rendered manifests; a new token-free
  share-attempt repository; gate private state; bounded WordPress hash pool.
- Verification provider for the current task: PERSONAL Codex GPT-6.1-Sol high only.
  This design pass runs no tests/builds/apps/network/remote jobs and spawns no helpers.
  Historical reports keep their original provider provenance. Later runner assignment
  and host slot are coordinator-owned, never an implicit Office/Luna fallback.
- Platform: macOS controller and Linux remote hosting; existing tailnet panel.
- Scope: direct uses limited to four, eight outstanding sign-ins per entry, existing
  gate bounds enforced by refusal without silent truncation. No recursive traversal or general health probing; only the explicit authenticated
  non-consuming companion availability probe is added.
- Constraints: placeholder hosts in committed content; no new live grants, migrations,
  deployments or applies. Full-stack thread exclusively owns fs-try/host mutations;
  shared dev and setup-plan remain untouched.

## Constitution check

Design is mapped to the constitution below; independent review and runtime proof remain pending.

1. Keep project/instance targeting explicit. Use the recorded remote/slug and manifest.
2. Extend existing registries through their APIs; default absent uses to empty.
3. Keep sb as the entry file; feature logic belongs in hosting modules and registered contracts.
4. Completion requires supported real runtime evidence. Unit checks alone cannot close
   this feature. Live sign-ins cannot be exercised without the owner's authorization.
5. Mutation recovery uses stable ids, durable attempt records and atomic writes. Docs
   and revision evidence land with code; deployed protocol upgrades remain owner actions.
6. Preserve existing hosting/login paths. No facade or provider is removed.

This task edits design only. It writes or runs no tests and certifies no checks from
concurrent helpers. Future implementation/verification order remains coordinator-owned.

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
specs/088-stack-page-access/           # design and acceptance contracts
```

Use the current panel poller and health cache. A missing stack is a normal page state.
Do not add imports of a facade or registry namespace prohibited by repo guidance.

## Design decisions

See [research.md](research.md), [data-model.md](data-model.md) and
[contracts/access.md](contracts/access.md). These are proposed until implementation
and verification. Earlier checkpoint reports are supplied historical evidence, not rerun here. Remote
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

For shared pages use the exact v2 page, complete coverage, action POST, child probe
and WP online claim in [contracts/access.md](contracts/access.md). Gate GET/HEAD never
issue sign-ins. Eight pending per-entry hashes and source lifetime checks apply to
fresh clicks. Legacy direct CLI singleton remains separate. Host-only sessions confer
no panel or sibling authority. WordPress callback needs independent authentication
review and owner sign-off before release; the exact source callback wire is recorded in access.md, with runtime parity unverified.

Dependency e04e293 is integrated locally by normal merge 93431f7. Allocate derived
l-12hex ids with the same MintedLink passed to later mint_link; final page travels in
that mint, companions first then parent. No attach, reservation or replay-success mint.
Durably persist safe original runtime/gate/key-cleanup identity before unknown-before-
keyinstall/mint. Per-parent attempt lock then sorted unique effect locks, with short
state snapshots and no shared lock nesting. List/revoke recover even without parent.
Never remint on uncertainty or clean a replacement slug by guess. Exact four-capability negotiation and schema-2 original cleanup descriptors are
recorded in access.md/data-model.md. Late controller result/error fields remain
pending owner consolidation, not new invented contracts.

## Phase manifest

One release phase, outcome: a tailnet user or teammate with one stack link can reach
all own/direct shared routes, inboxes and sign-ins, and the owner can revoke it.

- Scope: US1–US6, docs, revision evidence, regression and runtime proof.
- Assigned PRs: planned Sandbox `feat/stack-page-access`; no PR opened.
- Target release cycle: coordinated owner-approved dev integration, date unassigned.
- Dependencies/order: local short-link integration is complete; contract agreement →
  implementation across US1–US6 → actual supported workflows → focused regressions and
  required gates → independent source/authentication review → owner release sign-off.
- Acceptance: all spec success criteria; fresh non-tailnet browser exercises all
  own/shared paths; two sign-ins both work; parent revoke closes companions and unused
  sign-ins; recovery succeeds after partial failure and gate restart.
- Readiness: no feature acceptance declared. Source integration removes the dependency
  merge block; installed protocol support, authentication review and runtime acceptance
  remain unverified. No partial release promised.

Earlier local checkpoint work exists for panel/relationships/controller WordPress.
This pass neither reruns nor adopts its tests as current proof. All six stories remain
in one release. Follow [quickstart.md](quickstart.md), which is a future reviewable plan,
not authorization to execute. Current design provider is personal Sol high; historical
Office/other-provider review records are preserved in their original artifacts.

## Complexity tracking

No constitution exception. The new attempt repository is needed because a parent gate
can lose state while companion grants remain live. A gate-only revoke cannot meet
FR-029–FR-032. New modules separate this mechanism from CLI and HTTP presentation.

## Bounded source documentation reconciliation, 2026-11-11

Applied speckit-plan Phase 1 contract/data-model/quickstart reconciliation to the
existing 088 plan after metadata-only setup-plan. No specification, PRD, historical
review provenance or task marker is rewritten. Explicit task ownership excludes
agent-pointer regeneration; optional after_plan agent-context hook is skipped.
Source schema/runtime/WP/ledger were inspected, not executed. Constitution IV proof
and VI compatibility acceptance remain open: v1 WP action is explicitly unavailable,
v2 reissuance is required, and apply resets gate tmpfs. This is not runtime acceptance
or approval of that compatibility limitation for release. No lifecycle upgrade is run.
