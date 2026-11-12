# Product Requirements Draft: Permanent hosted WordPress login

**Status**: Refined
**Created**: 2026-11-12
**Last Refined**: 2026-11-12
**Input**: Add permanent links as an option in original Sandbox auto-login, on a new branch, keeping existing login working as is.
**Drafting Configuration**: Office Sol, medium
**Final Validation**: PASS; independent Office GPT-6-Luna high, 2026-11-12
**Validated On**: 2026-11-12
**Artifact Owner**: `speckit-refine`
**Next Stage**: `speckit-specify`

## Problem and Motivation

Hosted WordPress login links expire and work once. An operator repeatedly accessing a development site needs an explicitly chosen reusable link without installing a project-specific helper. Existing users must retain the current defaults.

## Users and Desired Outcomes

- Hosting operator: choose a reusable, non-expiring login link for the configured WordPress user.
- Existing caller: obtain the same time-limited single-use link without changing a command or configuration.

## Goals

- Explicit permanent selection permits repeated login after logout and over time.
- Existing local login, hosted one-time login, and other login-provider behavior remain unchanged.
- Replacing a link invalidates the previous link; malformed or invalid credentials cannot authenticate.

## Non-Goals

- Changing local login defaults, adding permanent share/gate/email-provider links, changing user privileges, or deploying this branch globally.
- Preserving multiple simultaneously active hosted WordPress links.

## Product Scenarios

### Scenario 1: Existing usage

- Starting state: configured deployed hosted WordPress target.
- User action: request a link without permanent selection.
- Expected outcome: existing expiry and single-use behavior, output, confirmation, and target selection remain.

### Scenario 2: Deliberate permanent usage

- Starting state: same target.
- User action: explicitly request and confirm a permanent link.
- Expected outcome: the link authenticates, then authenticates again after logout, without expiry or a use counter. Normal session lifetime is unchanged.

### Scenario 3: Invalid or replaced link

- User action: use a wrong key, a replaced link, or a link whose configured user no longer exists.
- Expected outcome: no authenticated session is created.

## Proposed Product Behavior

- Permanent behavior is off by default and selected for each issuance.
- Permanent selection overrides the manifest's ordinary default lifetime. Combining permanent selection with an explicitly supplied finite lifetime is refused before changing the current link. Unsupported login-provider modes refuse permanent requests without changing their state.
- Issuing any replacement invalidates the previous hosted WordPress link. Explicit operator removal of the installed login helper revokes link access. Removing only the manifest declaration is not a revocation action; issue a replacement link or remove the installed helper. Existing signed-in sessions retain normal WordPress behavior.
- A permanent key must not appear in HTTP request URLs, public status, retained output, or host state. Only the deliberate foreground issuance response, including its existing JSON form, may disclose a newly issued key to the operator. Later status, logs, saved host state, and retained job output must not contain it. Existing one-time output and JSON fields stay unchanged.
- Link previews and ordinary GET requests do not authenticate. The legitimate same-origin browser flow must work; foreign-origin submissions are refused.

## Constraints and Dependencies

- Extend the existing hosted WordPress integration; retain its configured user, route, service, ownership and confirmation controls.
- Existing source uses hash-only credential storage and replacement as revocation; retain both.
- New authentication behavior needs independent security review before release. No deployment or release is authorized by this feature request.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|---|---|---|---|
| Lifetime/use count | No expiry, unlimited reuse when selected | User requested permanent links and just verified that behavior | User in this conversation |
| Default | Existing behavior unchanged | Explicit compatibility requirement | User |
| Scope | Native hosted WordPress, not other providers | Original integration is being extended; avoid altering unrelated contracts | Existing source and bounded scope |
| Replacement | One current link per target | Preserve existing ownership and replacement model | Existing behavior |

## Open Questions

None. Unsupported provider modes refuse the option rather than changing their policies.

## Acceptance Outcomes

- An unchanged existing command still issues an expiring single-use link.
- A permanent link works for two sessions separated by logout and remains valid when time advances beyond the ordinary expiry. Its key is absent from initial and subsequent HTTP request targets, including query strings; the browser transport may be chosen during design.
- Link previews and foreign-origin requests cannot authenticate, consume, or revoke the permanent credential.
- An explicit lifetime conflicts with permanent selection before installation or state writes, while the configured default lifetime does not prevent permanent selection.
- Replacing the helper credential invalidates the previous link; removing the installed helper prevents login through it. Neither action claims to terminate existing WordPress sessions.
- Wrong keys, wrong-origin submissions, missing users, and replaced links cannot create a session.
- Conflicting or unsupported options cause no link installation or host-state mutation.
- Only the deliberately requested foreground issuance response may disclose the fresh credential. It is absent from later status, logs, host state, retained job output, evidence and commits.

## Risks and Assumptions

- **Risk**: a permanent administrator credential remains powerful until replaced or removed. Make this explicit in help and documentation.
- **Assumption**: the operator owns the declared target and already controls its WordPress login integration.
- **Risk**: the previous standalone helper's no-referrer policy broke browser login; compatibility must include actual browser behavior, not only a created grant.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: READY FOR SPECKIT
