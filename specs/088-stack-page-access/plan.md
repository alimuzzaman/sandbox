# Implementation plan: Stack page access

**Branch**: `feat/stack-page-access` | **Date**: 2026-11-13 | **Spec**: [spec.md](spec.md)

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
  share-attempt repository (v3 writes, conservative v2 reads); gate private state; bounded WordPress hash pool.
- Current implementation and independent review: Codex Office GPT-6.1-Sol high.
  Bounded tests and typechecks run through Codex Office GPT-6-Luna high helpers.
  Historical reports retain their actual provenance. Runtime work requires an
  explicit coordinator-owned slot; the queued position is not a slot grant.
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

Source for US1–US6 is present in the reconciled latest base cbda71e, including
short links e04e293 and the owner-panel closure. This branch preserves that source
and adds the previously separate behavioral regressions and design records.
Mocked checks establish only the behavior they exercise; runtime proof remains open.

## Project structure

```text
sandbox/hosting/stack_template.py       # template syntax and placeholder paths
sandbox/hosting/stack_registry.py       # stack uses and delivered revision
sandbox/hosting/panel_view.py          # shared read model
sandbox/hosting/panel_access.py        # recorded-target access orchestration (new)
sandbox/hosting/autologin.py           # bounded hash pool and WordPress renderer (new)
sandbox/hosting/share/attempts.py       # durable token-free attempt repository (new)
sandbox/hosting/share/controller.py     # physical ownership and bound effects
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
[contracts/access.md](contracts/access.md). They describe the integrated source.
Installed support remains unverified and no release is authorized. Earlier reports
remain historical evidence unless explicitly rerun against this reconciliation.

Controller sign-in resolves the stack's recorded rendered manifest. The CLI's
`--name` selection uses that resolver too; it must never fall back to cwd. The
manifest remains required, and a missing checkout/manifest gives an actionable
unavailable result. Panel polling reads declaration metadata only and never mints.

Keep uses in controller records initially, including the controller overlay in
stack list. Do not add a remote hoststore schema merely to display relationships.
A server-local panel uses the current applied owner_share projection, exact installed
gate/config/container bindings and fixed verbs; it never invents a controller remote.
See docs/owner-panel-sharing.md for the authority surface requiring the final review.

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

Dependency e04e293 is in origin/latest cbda71e and is reconciled by a normal merge. Allocate derived
l-12hex ids with the same MintedLink passed to later mint_link; final page travels in
that mint, companions first then parent. No attach, reservation or replay-success mint.
Durably persist safe original runtime/gate/key-cleanup identity before unknown-before-
keyinstall/mint. Per-parent attempt lock then sorted unique effect locks, with short
state snapshots and no shared lock nesting. List/revoke recover even without parent.
Never remint on uncertainty or clean a replacement slug by guess. Four feature
capabilities and bounded cleanup descriptors are recorded in access.md/data-model.md.
Owner-local operation also requires owner-panel-gate-v1 and exact running digests.
Controller list/revoke result fields are consolidated in access.md. A reloaded unknown
operation remains cleanup_pending until original completion can be established or
its unused capabilities expire; an absence probe cannot fence a late writer.

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

All six stories remain in one release. Follow [quickstart.md](quickstart.md), which
is a reviewable runtime plan, not authorization to execute. The owner approved the
stack one-link model and Tailnet-panel plus current applied agent_mint policy.
That approval does not extend to paused 090 authority or installed runtime acceptance.
The final review must cover admission, currentness, writer serialization, original
operation fences, callback claims, revocation and one-response token custody broadly.

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

## Reconciliation, 2026-11-13

Spec Kit specify → plan → tasks updated the existing 088 records using the owner
decisions and cbda71e source. PRD/readiness provenance is preserved. Optional agent
context regeneration is skipped to preserve latest pointers. Existing source carries
the UI bundle; this reconciliation changes no UI production code and needs no rebuild.
Runtime acceptance and the fresh broad authentication review are still release gates.

The broad R1 auth review returned NOT SAFE. Controller sharing must now prove
current running applied policy/config/target digests, hold the physical apply lease
and use immutable services. Durable v3 settlement must not trust legacy v2 revoke
bits. Per-entry TTL and route action association are also enforced. New fixture and
fresh independent review evidence supersede earlier check counts only when recorded.
