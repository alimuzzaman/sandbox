# Data model

All additions are proposed. Existing readers default absent relationship fields to empty.

## Stack relationship

Template `stack.uses` is a list of at most four safe same-remote stack slugs. Reject
non-list/non-string values, invalid names, duplicate, self, missing and over-limit uses
before side effects. References are direct and name-based; replacement follows the name.
StackRecord gains `uses: list[str]`; reapply replaces the list, delivery preserves it.
Controller stack-list overlay returns uses; no remote lifetime schema change is needed.
Down returns `used_by` without blocking removal. Missing referenced records stay visible.

## Stack view

A stack view keeps remote, source and slug identity; routes retain their existing
state, health and linked-site identity. Add explicit revision evidence status
`verified | unverified | not_delivered`, direct uses, inbox availability and sign-in
availability/reason. A shared section resolves its referenced view once, without
including that view's shared sections. The server-only view has no mint authority.

## Outstanding WordPress sign-in

Remote protected pool stores at most eight records of SHA-256 hash, user, issued_at
and expires_at. No raw token is stored. Prune expired records on mint and consumption;
drop the oldest unused record when full. A connection-scoped database advisory lock
covers read/update/claim of the WordPress site option, with cache invalidation after
lock acquisition. Plugin replacement does not replace the option.
Missing or malformed pool fails closed. Link goes from issued to consumed or expired;
replay has no remaining matching record. No pool contents enter panel polling or audit.

## Share attempt

Repository is owned by a dedicated module, not raw JSON readers in callers. One
attempt records id, parent remote/target/stack, creation/expiry, phase, and planned
parent/companion grants (target, gate entry, id, expiry, cleanup status). Never tokens.
Transitions: prepared → minting → active; minting failure → cleanup_pending → revoked;
active → revoking → revoked or cleanup_pending. Persist before each external effect.
Unknown remote acceptance remains cleanup_pending and must be reconciled by stable id.
A missing parent gate does not delete companion identities. Cleanup is idempotent;
list includes incomplete attempts; next create cleans them first or refuses to mint.

## Gate stack record

Extend the existing validated stack record with route access declarations, direct
companion page descriptors and per-click sign-in actions. Companion page records have
no companions. Private gate state may contain companion tokens; controller records,
audit and job output may not. Bound all additions to existing gate size/count limits.
