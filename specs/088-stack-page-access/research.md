# Research decisions

Historical planning evidence: source at 9293056 and read-only scout/seam reports.
Current reconciliation additionally reads locally integrated short-link APIs at
93431f7 and the supplied integration-design/shortmerge handoff. No runtime behavior
was exercised during planning or reconciliation.

## Direct stack relationships

Decision: optional `stack.uses: [dev]`, validated during plan and under the confirm
registry lock, persisted on StackRecord. Internally use `stack_uses` where a function
also handles a share's redemption count. Existing records default to empty.
Rationale: the owner asks for recorded same-remote stacks, not hostname duplication.
Alternative: copy route hostnames into every template. Rejected because routes and
revisions would drift. Cycles are harmless because traversal is one level.

## Panel and revision

Decision: add a clean remote/slug route and extend the existing view. Use
stack_registry.delivered_commit with the job-refined last apply state, as stack list
does; surface legacy evidence as unverified. Linked sites keep their own commit.
Alternative: use the requested manifest revision. Rejected because a failed delivery
can leave the previous revision serving.

## Recorded manifest resolution

Decision: load the selected record's manifest and validate it normally. Existing
validate_loaded handles rendered stack manifests and enforces their stack-directory
location. Do not bypass checkout validation for a mint; a missing checkout is an
unavailable action. Share target resolution already has a recorded-stack path to reuse.
Alternative: reconstruct from host state or run CLI in cwd. Rejected because neither
reliably selects the recorded target's service, path and user.

## WordPress sign-ins

Decision: replace the single embedded hash with a protected bounded remote hash pool
under a stable lock. Mint and consume atomically; at most eight live hashes, each
with its own expiry and user. Fragment opener posts the token. Keep declared service,
container_path and request_path. Shared-page WP uses a separate gate hash pool and authenticated online claim;
controller/panel WordPress keeps the database-backed pool.
Rationale: rewriting one hash invalidates another visitor's unused link.
Alternative: embed an accumulating list on the controller. Rejected because consumption
is remote and controller polling must never expose login material.

Implementation refinement: store the pool in a WordPress site option and use
MySQL/MariaDB connection-scoped advisory locking. An adjacent PHP data file would
require assumptions about the PHP filesystem user and lose state on container
recreation. WordPress database storage avoids both issues and preserves consumed
links across plugin replacement. Supported runtime proof is still outstanding.

## Companion grants and revocation

Decision: reuse host-only gate sessions; mint one direct companion on each used stack's
gate, bound by the earliest development window/parent expiry. A token-free durable
attempt record is the source for rollback and revoke. Acquire target locks in sorted
order. Never return a parent link before every grant with its final page succeeds.
Alternative: parent-domain cookies or proxying the panel. Rejected because the former
widens host authority and the latter exposes a private mutating API.

## Dependency and authorization

Local HEAD 93431f7 includes landed e04e293 by normal merge. The shortmerge handoff
reports remote landing and source review; this pass made no remote observation.
Short API source reads confirm derived l-12hex id, same preallocated MintedLink,
final page in same mint and duplicate refusal rather than idempotent replay. Historical
fetch/check results above retain provenance and do not describe current remote state.

Historical design provider: personal Codex Sol high. Current work is Codex Office Sol high; bounded checks use Office Luna high, with no runtime runs authorized.
Source mechanism is authorized; independent authentication review and owner sign-off
remain required before release. No live authority is sought or implied by this design.

## Settled integration decisions, 2026-11-11

- Explicit route_sign_in to stack_sign_in or impossible-entry sentinel `$wordpress`;
  inbox_sign_in explicitly selects inbox_gate. Anonymous absence still refuses known
  restrictions or metadata gaps. Enumerate all discoverable gaps before effects.
- Exact new sandbox.share-stack.v2, retained v1 reader and unchanged share-link.v2.
  Read-only GET/HEAD; guarded POST click actions; host-only child navigation; child
  authenticated non-consuming availability probe and never restart remint.
- Per-entry eight pending gate hashes, bounded expiry and source grant binding.
  Preserve direct CLI singleton separately. Shared WP validates signed token then
  claims online at a fixed configured HTTPS gate endpoint using purpose-separated
  HMAC, nonce/timestamp and signed receipt. Fail closed and remove per-link key on revoke.
- Persist bounded safe original cleanup identity before unknown/key install/mint;
  no manifests or secret material. Attempt lock then sorted unique effect locks,
  short snapshots and revalidation, no nested shared state.lock. Missing parent and
  replaced slug cannot erase cleanup obligations.

These replace earlier speculative shorthand, not the accepted user goals. Exact
schema is in contracts/access.md. Historical planning note, superseded for stable source wire by contracts/access.md: Still requiring owning agreement: callback path,
canonical HMAC/receipt/replay window and finite transport limits; version/capability
handshake; durable cleanup descriptor/API integration. None is an installed feature.

## Source reconciliation evidence, 2026-11-11

Runtime, WP claim renderer, v2 validator/coverage and attempts schema-2 source plus
their five resume reports were read without execution. Stable exact wire is now
recorded in contracts/access.md: flat descriptors, four capabilities, guarded
POST /__sandbox/stack/sign-in, paired child metadata probe and signed WP hash claim.
Earlier proposed nested pages/available endpoint/response fields are not contracts.
Late controller wire/result consolidation remains pending. Source presence is not
installed proof; checks/review/runtime acceptance remain open. Historical decisions
and review/provider provenance above are retained as historical evidence.


## Bounded linked-route intent contract, 2026-11-11

Coordinator selected explicit `share.linked_access: {hostname: open}` author intent,
consistent with anonymous unmapped own routes. The broader linked-access-writer
recommendation is research data and is not adopted. No deployment writer, proof
binding, generation or new freshness guarantee is introduced. Up to 16 canonical
allowed-suffix exact DNS keys, literal open only; template placeholders are permitted
in hostname keys using the same approved tokens with rendered collision refusal.
Values stay literal. Resolve against actual frozen TargetContext linked rows;
missing declarations and bare row access=open remain unknown, unused declarations
and known restrictions refuse before effects. Use each linked row's own valid commit
and state, never the enclosing stack's revision or a verified claim. Public linked
WordPress does not create an admin sign-in. Protected linked apps remain unsupported.

This contract declares anonymous normal-route intent plus known controls. It does
not measure anonymous application usability or detect out-of-band DB/auth changes.
Existing frozen manifest/stack-record revalidation under effect leases remains the
source consistency boundary. Definitions were added without execution; prior goals,
check states, reviews and runtime acceptance requirements remain unchanged.

## Reconciliation decision, 2026-11-13

Use the owner-sharing closure from origin/latest cbda71e. Preserve its completed-
transport callback and unknown-operation fences. Do not infer original completion
from a missing gate id after a crash. The owner approved panel admission plus current
applied agent_mint; fixed verbs and exact running bindings still gate every effect.
Paused 090 source is excluded. Broad independent review and runtime proof remain open.
