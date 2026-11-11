# Data model

Current design targets only. Exact page shape and wire status are in
[contracts/access.md](contracts/access.md); no runtime acceptance is claimed.

## Stack relationship and view

Optional stack.uses defaults empty, at most four distinct same-remote recorded slugs,
no self-reference; direct expansion once, cycles allowed. New shares follow names;
durable old attempts keep original identity. Registry APIs own persistence/reapply/
delivery and list overlay. Down reports used_by without blocking. Missing references
stay visible. Stack views retain remote/source/slug, own and linked route identity,
state/health and verified|unverified|not_delivered revision evidence. Server-only
views have no controller mint authority. Panel inbox discovery uses recorded mailpit
tailnet upstream metadata, distinct from recipient inbox authorization.

## Declared access and coverage

route_sign_in maps served hostnames to declared stack_sign_in entries or `$wordpress`.
Absent mapping explicitly declares anonymous access, subject to known restrictions
and adequate metadata. Optional share.linked_access defaults to {}, with at most 16
exact canonical allowed-suffix DNS keys mapped only to literal open. This is explicit
author intent for recorded linked normal routes. It provides no measured anonymous
usability, application-policy freshness, out-of-band auth detection or linked admin
authority. Bare record access=open is insufficient; undeclared links stay unknown,
unused declaration keys are gaps, and known restrictions still refuse. Linked rows use
only their own valid commit/state, otherwise null/unknown, with no parent fallback.
The frozen manifest/record is revalidated under current effect leases before effects.
No deployment writer or access-generation entity is introduced.
inbox_sign_in explicitly selects a stack_sign_in inbox_gate
when mailpit exists. stack_sign_in parses unique magic_link/inbox_gate names; v2 builder/validator caps
actions at 16, separately from the eight-pending-token pool per logical entry.
Coverage enumerates parent plus at most four direct children completely before any
key/grant effect. Retain per-stack rows. Every discoverable gap includes fix guidance;
never filter/truncate. Common expiry clamps all grants to the earliest window.

## Outstanding sign-ins

Controller/panel WordPress pool is a WordPress site option of at most eight hashes,
user/issued_at/expiry. Connection-scoped DB advisory lock serializes prune/update/
consume; invalidate option cache after lock acquisition. Plugin replacement preserves
this option. Missing/malformed state fails closed.

Shared-page gate pools are separate: per entry at most eight pending token hashes
across source grants, source entry/link, kid for WP, issued_at/expiry and deterministic
oldest eviction. Expiry <=min(now+600,entry ttl,source,session,window). Atomic consume
with source revalidation; restart loses state and denies. Legacy direct CLI singleton
remains explicit and separate. Shared WP plugin verifies token then authenticates a
fixed-endpoint online claim and signed nonce-bound receipt before auth. This gate pool
is distinct from controller durable attempts: hashes are permitted only in private
runtime pools, never in attempts, callback logs or audit.

## Durable attempt

Dedicated repository owns allowlisted parsed values and atomic private fsync writes.
Attempt: id, original parent target/stack, created_at/expires_at, phase, parent grant
and at most four companion grants. Grant: original remote/project/environment identity,
entry/id/expiry/disposition, safe gate hostname/origin, runtime incarnation/fingerprint,
and bounded WP cleanup descriptor with declared service/container plugin path and kid.
Ledger envelope is EXACT `{version:2,attempts:[...]}`; v1 refuses, no migration.
Grant adds `cleanup`, `gate_revoked`, `wordpress_revoked`. Cleanup EXACT
`{gate_hostname,identity,compose,wordpress}`; identity EXACT `{kind,value}`, copy_id
8 lowercase hex or runtime_fingerprint 64 lowercase hex. Compose EXACT
`{project_name,files,override_path,env_path}` retains fully joined original files in
precedence order. WP null or EXACT `{service,container_path,key_id,expires_at,protocol}`,
protocol `sandbox-share-login.v1`, key_id link suffix and expiry original grant expiry.
No whole manifests, token/hash/key/email/companion URL. The runtime fingerprint is
not a bearer hash. Reading a descriptor or comparing it against itself does not prove
remote identity. Independently observe the original incarnation covering ALL addressed
gate/WP services under lock; a slug, manifest hash or commit is insufficient.
Missing proof, missing original files or replacement retains cleanup_pending.
`cleanup_for` checks equality but does not probe remote runtime. Gate revoke and
`confirm_wordpress_removed` are separate facts. Global `list_recorded` and
`find_recorded_parent_link` support recovery before live parent resolution.
Late controller serialization/result fields remain pending consolidation.

Phases prepared → minting → active; mint failure → cleanup_pending; active → revoking;
cleanup resolves revoked/expired only with evidence. Per-grant planned → unknown BEFORE
key install/mint → live on validated receipt, then revoked/expired after cleanup.
Gate absence never means WP key cleanup complete. Unknown/keycleanup-incomplete stays
visible, even without parent manifest. Reconcile original id, never remint. Slug
replacement cannot redirect destructive cleanup. Expiry only discharges a grant when
no remaining unused capability can authenticate.

Per-parent attempt lock precedes sorted unique target effect locks. Brief owned
state snapshots/revalidation do not hold shared state.lock over effects. If identities
change, drop locks and recompute. Next create cleans incomplete attempts or refuses.
List/revoke load durable attempts before relying on parent resolution.

## Gate page and capabilities

New exact sandbox.share-stack.v2 page has entry/link/title/hosts/notes/wordpress/sign_in/
inbox/companions as defined in access.md. Private companion descriptor contains its
original token/id/expiry and flat bounded title/hosts/notes, never a nested page. Child pages have empty companions. Parent mint
contains final descriptors in the same payload. Gate v1 reader remains; share-link.v2
is unchanged. GET/HEAD render without issuing sign-ins. Guarded POST actions return
fresh sign-in or existing companion short URL once; authenticated child availability
probe consumes nothing. Cookies remain host-only and authority stays grant-scoped.

Short ids derive from their token hash, and the SAME preallocated MintedLink is
passed to mint_link. There is no reservation/attach/idempotent-mint state. Unknown
acceptance goes to cleanup by original id. Apply tmpfs loss is not grant survival.
