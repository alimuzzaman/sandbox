# Access contracts (proposed)

This document is a design contract, not evidence that a field or endpoint is implemented.

## Template

Merge this block into a complete existing WordPress hosting template:

```yaml
stack:
  slug: '^[a-z0-9][a-z0-9-]{0,30}$'
  ports: [mailpit]
  dev_branch: dev
  uses: [dev]
```

`uses` defaults to `[]`; it contains literal recorded stack slugs on the selected
remote in the selected controller registry, at most four, unique, excluding self. Each slug uses lowercase ASCII
letters/digits/hyphens, starts alphanumeric and has 1–63 characters. Both plan and
confirm refuse an unknown referenced stack before writes. Distinct proposed refusal
codes: `stack_uses_invalid`, `stack_uses_limit`, `stack_uses_duplicate`,
`stack_uses_self`, `stack_uses_missing`. Confirm repeats the existence check under
the registry lock. No transitive expansion; cycles accepted. Combined self-contained
stacks omit uses. Relationship declaration creates no remote resources.

Inbox discovery uses port name `mailpit` and an existing same-remote tailnet-name
record for that port. Without one show setup guidance. No inbox-hostname template
key is introduced. Setup wrappers retain explicit tailnet-name provisioning.

Template placeholder additions are limited to environment share.hostname,
autologin.entries.*.hostname and autologin.entries.*.magic_link.{request_origin,
link_origin,link_pattern,required_params.*}. Preserve literal escaping for replacements
inside a regex. Do not permit placeholders elsewhere in those declarations.

Route-to-sign-in declaration proposed as `share.route_sign_in`, a hostname-to-entry
mapping. Values must name a declared stack_sign_in entry; reject unknown hostnames
or entries. Hostname mapping keys can use stack placeholders only here. Exact syntax
must be reconciled with short-link branch validators before this becomes implemented.
An unmapped route is declared anonymously usable. Template authors must map every
route requiring application sign-in; Sandbox cannot infer application auth.

## Panel

Clean UI route: `/hosting/<remote>/<slug>`. Resolve from the existing hosting view.
Unknown identity shows a missing-stack state and a link to Hosting.

`POST /api/hosting/sign-in` takes exactly `{remote, stack}`. It requires the same
Host/Origin, JSON content type, bounded body and `X-Sandbox-Panel: share` guard as the
existing share endpoint. Validate names and reject extra keys before resolution.
Success is a one-shot no-store response `{ok, url, expires_at, one_time, user}`.
Failures contain a safe code and message, no command output, credentials or URL.
Never place this response in the polling store or browser persistence. Open a blank
new tab on click before awaiting the request, navigate it on success and close on failure.
The current controller implementation refuses a private primary route rather than
minting a link that cannot reach WordPress. Public gate handoff remains dependent
on the short-link integration. Server-only panels have no controller mint authority.

Use the same recorded target operation for `sb host login-url --remote R --name S
--confirm`. Preserve existing plain-manifest mode when no named target is selected.
Reject selection conflicts and unavailable manifest/private targets before minting.
No GET, list flag or invalid request may mint. Audit remote/target/action/outcome only.

## Teammate pages (blocked on short share links)

Use the short link format from the merged dependency. Preserve host-only sessions.
Every route has open or named sign-in access; inbox requires a site_gate sign-in.
Preflight parent and every direct used stack before any grant: suffix/window guards,
agent_mint, no Basic Auth, no inaccessible private route, and declared inbox access.
Return every gap with its fix. Clamp all grants to the earliest expiry.

Mint parent and companions under one durable attempt. Attach companions only to the
parent gate's private state. Companion pages show no nested Shared section. Per-click
sign-ins expire within ten minutes, bind to their source grant and are invalid after
revoke. Report a lost companion as unavailable; do not silently mint another.

Revoke/list use the durable token-free attempt repository. Partial revoke returns
`ok: false`, attempt id, and `still_live` grant ids/targets/expiries. Repeat revoke
reconciles every grant including those whose mint acceptance was unknown. Unknown
parent state is not evidence that companions are gone. Completed application sessions
keep their own lifetime. Each companion mint/revoke and sign-in is audited without tokens.

## Compatibility and revision evidence

Additive controller relationships need no hoststore schema change. Gate wire changes
require a schema/revision update in the owning contract, matching docs/tests and
explicit version checks. Upgrade only through supported lifecycle commands after
owner approval; no raw remote edits or automatic migration in this task.
