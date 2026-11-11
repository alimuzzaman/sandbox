# Research decisions

Evidence: existing source at 9293056 and the read-only scout/seam reports in the
shared scratch directory. No runtime behavior was exercised during planning.

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
container_path and request_path. Existing shared-page signed-key support remains
separate until the short-link integration is available.
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
order. Never return a parent link before every grant and page attachment succeeds.
Alternative: parent-domain cookies or proxying the panel. Rejected because the former
widens host authority and the latter exposes a private mutating API.

## Dependency and authorization

Fresh fetch on 2026-11-11: origin/latest e46777b does not include short-share-links
at e04e293. No gate edits before merge/rebase. No remote upgrade or migration is
necessary to author design or controller relationship changes. Any protocol/schema
change needs matching revision evidence, tests and docs before a supported owner-run
upgrade. Test execution uses Luna, remote mutations remain exclusive to full-stack.
