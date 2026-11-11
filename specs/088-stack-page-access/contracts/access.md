# Access contracts

Current design, reconciled 2026-11-11 by PERSONAL Codex GPT-6.1-Sol high.
These are implementation targets, not installed capabilities. Historical reviews keep
original provenance. Full US1–US6 acceptance, independent authentication review and
owner sign-off remain required before release.

## Template and complete coverage

`stack.uses` defaults to `[]`: at most four unique literal same-remote recorded slugs,
excluding self, lowercase ASCII alphanumeric/hyphen, 1–63 characters, starting
alphanumeric. Plan and confirm validate existence; confirm revalidates under the
registry API lock. Refusals remain `stack_uses_invalid`, `stack_uses_limit`,
`stack_uses_duplicate`, `stack_uses_self`, `stack_uses_missing`. Expand direct uses once,
never recursively; cycles are allowed. New shares follow current names. Old attempts
remain pinned to original identities even when a slug is replaced.

`share.stack_sign_in` parses as a list of unique declared `magic_link` or `inbox_gate`
entry names. The v2 page builder/validator bounds its actions to 16; the parser does
not itself enforce that aggregate bound. `share.route_sign_in` defaults to `{}` and maps declared normalized served
hostnames to a name in that list or the reserved literal `$wordpress`. The sentinel
cannot match the entry-name grammar. Reject unknown hostname/entry, normalization
collisions, wrong destination and unsupported modes. A magic-link destination is its
link_origin host, not its request_origin or label. An inbox_gate destination is its
hostname. `$wordpress` requires declared WordPress autologin, a nonprivate primary
route and that exact primary destination; WordPress admin can also be a separate action.

An absent own-route mapping declares anonymous normal-route intent. Known Basic Auth,
private or inaccessible routes still refuse. This is author intent plus known controls,
not measured anonymous usability or a guarantee that application authentication cannot
change outside Sandbox. A standalone site_gate route is a gap; a stack cookie does not
confer that site's authority.

Optional `share.linked_access` defaults to `{}` and maps at most 16 exact canonical
lowercase DNS hostnames, each at most 253 characters and covered by
`allowed_host_suffixes`, to the literal `open`. No URLs, tokens, conditions, wildcards,
normalization aliases or protected access modes. This declares an anonymous normal
route only for the exact linked hostname recorded in the frozen `TargetContext` link
rows. It is separate from `route_sign_in`, which maps this stack's own served routes.
An absent declaration remains unknown even when a legacy linked row says `access=open`.
Unused declarations refuse with deterministic `linked_access_unrecorded` gaps; malformed,
off-suffix and over-limit declarations refuse, and recorded malformed/off-suffix rows
remain coverage gaps. Known linked restrictions still refuse. Protected linked apps
are unsupported; configure anonymous normal-route access before declaring `open`.
Linking public WordPress never adopts its admin sign-in or creates a linked admin action.

Linked rows retain only their own valid 40-character lowercase hex commit and allowed
state, otherwise `null`/`unknown`. Never substitute the parent revision/state or label
a declaration verified. The existing frozen manifest and stack-record revalidation
under target effect leases applies before effects. No deployment access writer,
access generation or old proof metadata is required by this intent contract; it does
not detect out-of-band application DB/auth changes or prove freshness of app policy.
Coverage guidance points to `share.linked_access`, never an imaginary redeploy proof.

`share.inbox_sign_in` is a literal entry name, required whenever `ports.mailpit`
exists. It must name a declared stack_sign_in `inbox_gate`, never site_gate. Tailnet
inbox names are panel discovery only, not teammate access proof. Panel guidance uses
recorded same-remote tailnet upstream metadata; no new inbox-hostname template field.

Permit placeholders only in environment share.hostname, route_sign_in and linked_access hostname keys,
autologin.entries.*.hostname and magic_link request_origin/link_origin/link_pattern/
required_params values. Regex replacements remain literal via re.escape. Entry names
and inbox_sign_in are literal. Preserve other existing approved template paths.

Enumerate all own and direct used stack routes, linked sites, inboxes and sign-ins
before any key installation or grant. Keep rows scoped by stack even for equal hosts.
The pure coverage API returns sorted, deduplicated `CoverageGap` values with
`remote,slug,hostname,code,guidance`; `CoverageResult.require_complete` raises
`share_coverage_incomplete`. The controller raises `stack_share_coverage_incomplete`
with sorted `gaps` rows carrying `stack,target,piece,code,fix` and optional `hostname`;
the dashboard projects the same safe fields and allowlists `piece`/`code`. Collect every discoverable gap: missing stack,
manifest, gate or agent_mint; suffix/window; Basic Auth/private/unreachable; missing
metadata/inbox mapping; invalid destination; unsupported site_gate; host/action/byte
limits. Malformed structures that prevent enumeration get a bounded parse refusal.
Never silently filter a route, truncate a list or mint a partial page.

All grants share E=min(requested expiry,parent window,each companion window), with
at least 60 seconds left. Each enabled_until must be unexpired and within 30 days.
Report clamp reasons. Companions require agent mint without terminal prompts.

## Landed short-link API and durable orchestration

Source dependency e04e293 is included by normal merge 93431f7. This is local source
integration, not installed revision evidence. `new_token()` yields a 22-character
128-bit token; `short_link_id(token)` is `l-` + SHA256(ascii token)[:12].
`share_url(hostname,token)` takes two arguments. Preallocate through
`new_link(validated,request)` or share.mint and pass the SAME MintedLink object to
`mint_link(validated,entry,request,minted=original,stack_record=final_page)`.
No independent id, reservation, attach-later operation or idempotent mint exists.

Supported gate management verbs are site capabilities/mint/list/revoke. Mint stdin is exactly
`{link:<sandbox.share-link.v2>,stack:<page|null>}`. Stack kind requires its matching
link id and `/__sandbox/stack` scope in that same mint; site kind requires stack=null.
Duplicate id/hash returns site_record_invalid, not replay success. Validate reply id,
expiry and uses against the prepared envelope. Lost/malformed replies mean unknown.
The existing share-link.v2 exact keys and retained legacy link/session readers stay
unchanged. Mint companions first and parent last with its final private page.

Acquire per-original-parent attempt lock, then sorted unique target EFFECT locks by
remote/project/environment. Never nest host writer callbacks or shared state.lock.
Use short owned registry/state snapshots and revalidate identity/declarations before
effects. If target set changes, release and recompute, never extend locks out of order.
No remote call under shared state.lock.

Persist/fsync attempt and bounded original safe gate/runtime identity plus WordPress
cleanup descriptor BEFORE unknown-before-keyinstall/mint. Do not persist entire
manifests, tokens, token hashes, signing keys, emails or companion URLs. Mark unknown
before the first external effect, then live only on exact confirmed reply. Persist
active before returning the one-shot parent URL. On uncertainty, reconcile/list/revoke
original ids; never remint. Failed persistence before effects means zero effects.
Cleanup all live/unknown grants on failure. Load durable attempts for list/revoke even
when parent state/manifest is gone. Gate absence and key cleanup are separate facts;
retain unknown and keycleanup-incomplete states. Never guess destructively at a new
runtime with an old slug. Next create cleans incomplete attempts or refuses.
Attempt storage is an exact version-2 `{version,attempts}` envelope, bounded to 1 MiB
and 256 records. Each attempt has `id,parent,created_at,expires_at,phase,grants`; each
grant has `target,entry,id,expires_at,disposition,cleanup,gate_revoked,wordpress_revoked`.
The cleanup descriptor is the closed `gate_hostname,identity,compose,wordpress` shape;
it stores paths/identity only, never config contents or credentials. Persisted terminal
phases are `revoked`/`expired`; incomplete phases include `prepared`, `minting`,
`revoking`, and `cleanup_pending`.

Controller attempt summaries return `attempt_id,phase,link_id,expires_at,target,stack`
and `grants[{target,entry,id,expires_at,status,gate_revoked,wordpress_revoked}]`.
Pending cleanup returns `ok:false,phase:cleanup_pending,code:share_cleanup_pending`, a
fixed message, and `still_live[{target,entry,id,expires_at,status,reason_code}]`.
Stack list returns `links,sessions,attempts`; partial reasons are
`current_parent_unavailable` or `durable_cleanup_pending`. Stack-wide revoke returns
`ok,target,entry,revoked_links,revoked_sessions,revoked_keys`; partial results add
`phase,code,message,still_live,partial_reasons`, and the first recorded `attempt_id`
when available. It reconciles recorded original ids plus individually selected legacy
grants from the current parent gate; it never issues a gate-wide `all` request.

CLI refusal JSON is `{ok:false,code,message}`; coverage failures additionally carry
`gaps` with `stack,target,piece,code,fix` and optional `hostname`. Create/revoke
cleanup-pending results exit nonzero. Stack list may exit zero despite JSON `ok:false`;
callers must inspect `ok` and `partial_reasons`. The panel API list projects safe
attempt ids, phases, link ids, expiry and bounded still-live rows; it returns HTTP 200
only for complete evidence and 409 for partial or invalid evidence. It omits cleanup
descriptors. Expiry clamp reporting is the boolean `expires_clamped`, not a reason list.
Established application sessions retain their own lifetime.

## Exact source sandbox.share-stack.v2 page schema

`stack_page.valid_stack_v2` is authoritative for source shape; the standalone gate
mirrors it. Every object rejects extra/missing keys. These are private gate records,
not public list, polling or attempt records. Source parity requires separate checks.

- Page keys EXACTLY `{schema,entry,link,title,hosts,notes,wordpress,sign_in,inbox,companions}`.
  Schema is `sandbox.share-stack.v2`; entry matches `[a-z][a-z0-9-]{0,31}`;
  link is `l-` plus 12 or legacy 32 lowercase hex. Both bind to the enclosing grant.
  Title is 1–63 printable characters. Notes are at most 16 printable 1–512 character
  strings. Canonical `json.dumps(ensure_ascii=True,separators=(',',':'))` UTF-8 size
  is at most 32768 bytes, not the retained v1 16384-byte limit.
- Hosts: at most 16 unique rows, EXACT `{hostname,role,probe,commit,state,access}`.
  Hostname is exact lowercase DNS, at most 253 characters; role printable 1–32;
  probe `internal|public`; commit null or full 40 lowercase hex delivered revision;
  state `unknown|serving|redirecting|applying|failed|instance_not_found`.
  Access is `open`, `$wordpress`, or a listed sign-in name. Anonymous is `open`,
  never null. Displayed revision is evidence only, not runtime identity proof.
- Sign-in: at most 16 distinct rows, EXACT `{entry}` or `{entry,email}`. Emails use
  the existing validator and declared denied-address checks. Entry names are literal.
- WordPress: null or EXACT `{kid,key,url,claim_origin}`. Kid equals link suffix,
  key is 64 lowercase hex. URL is HTTPS with a safe absolute path and no credentials,
  port, query, fragment or dot segments. Claim origin is exact HTTPS DNS origin,
  no port/path/userinfo; gate binds it to its own configured hostname.
- Inbox: null or EXACT `{entry}` naming a listed inbox action without email.
  Declaration validation establishes inbox_gate mode; schema alone does not.
- Companions: at most four FLAT descriptors, EXACT
  `{slug,target,hostname,link_id,expires_at,token,title,hosts,notes}`. No nested page.
  Slug is lowercase alphanumeric/hyphen, 1–63, starting alphanumeric; target is a
  unique safe identity label 1–192 using alphanumeric, dot, underscore, slash and
  hyphen, without empty/dot/dotdot segments. It is display identity, not cleanup proof.
  Hostname is the child gate. Token is 22 base64url characters; link_id MUST equal
  `short_link_id(token)`. Expiry is integer `0 < expires_at < 10**12`; orchestration
  sets a common expiry and runtime checks equality to source expiry on mint.
  Title/hosts/notes have the bounds above. Child action names are display metadata;
  child page owns execution. Descriptor hosts/title/notes must match the stored child
  page via the authenticated probe. Child pages have empty companions.

All destinations must satisfy the declared suffix/window and action configuration,
not merely syntactic schema validity. `prepare_pages` covers parent and every direct
child before key/grant effects; no partial or silently truncated page is success.

## Capability and compatibility boundary

Before key installation or grant effects require ALL FOUR installed capabilities:
`stack-page-v2`, `stack-signin-pool-v1`, `wordpress-claim-v1`, `companion-status-v1`.
`site capabilities ENTRY` returns EXACT `{ok:true,capabilities:[...],stack_schemas:
["sandbox.share-stack.v1","sandbox.share-stack.v2"],link_schemas:["sandbox.share-link.v2"]}`.
The source branch's capability response is not installed support or revision proof.
Installed controller/gate revision and capability evidence must be observed through a
supported lifecycle/status command; that exact command and target remain UNKNOWN until
the owner authorizes the isolated fixture.
Old gates must refuse before effects. Link schema `sandbox.share-link.v2` is unchanged.
Existing 32-live/128-stored link and 512-session limits remain.

V1 pages remain readable while their tmpfs records remain present; GET/HEAD no longer
issue sign-ins. V1 WordPress actions are explicitly unavailable because the old
stateless plugin cannot enforce eviction. Reissue v2 links with the claim plugin. Plain
legacy CLI singleton issuance remains independent. Applying a new runtime resets gate
tmpfs links, sessions, stack pages, and pools, including legacy records; legacy readers
do not make those records survive apply. No silent migration or rollout is implied.

## Gate HTTP actions and sign-in pools

GET/HEAD `/__sandbox/stack` render metadata/buttons only. POST
`/__sandbox/stack/sign-in` takes EXACT `{action,csrf}`, at most 1024 bytes, exact
`Content-Type: application/json`, `X-Sandbox-Stack-Action: 1`, exact configured HTTPS
Origin, live host-only stack session and session-bound CSRF. Optional Sec-Fetch-Site
must be same-origin. Duplicate fields/JSON keys, transfer encoding, query variants,
foreign/missing Origin or caller-supplied destination/email refuse before effects.

Actions are a declared entry name, `$wordpress`, `companion:N` or `companion-status:N`,
where N is a declared zero-based companion index, 0–3. Fresh sign-in/navigation
success is EXACT `{ok:true,url}`; status success EXACT `{ok:true,available:true}`.
Responses are no-store. URL exists only in the one-shot action response and immediate
navigation, never token-bearing DOM, persistent storage, query or audit. The script
opens a blank tab synchronously and clears opener before fetch. Generic refusal is
404; authenticated action rate limit is 30/minute. Browser behavior remains unverified.

Parent rechecks source/session after body, child probe and before returning a URL.
Navigation returns the EXISTING child `/s/#<token>` URL, never remints. Its normal
opener creates the child's own host-only cookie; parent cookie grants no child,
site_gate, panel or proxy authority. A one-shot response is not a single-use child grant.

Child probe POST `/__sandbox/stack/companion-status` takes EXACT
`{token,link_id,expires_at,title,hosts,notes}`, JSON at most 32768 bytes, with
`X-Sandbox-Companion-Probe: 1`. Token travels in BODY ONLY. Child validates the live,
unspent grant, v2 page, matching id/expiry and paired metadata/hash proof; this is
more than an opener HTTP 200. Success EXACT `{ok:true,available:true,link_id,expires_at}`;
otherwise generic 404, bounded probe rate 120/minute. No use consumption/session/mint.
Parent uses fixed declared child HTTPS hostname, TLS verification, no redirects/cookies,
timeout 5 seconds, response cap 2048 bytes and no retries. Lost/replaced child state
means unavailable and requires explicit new issuance. No fallback/remint.

Private process-local pools retain at most eight pending hashes per logical entry
across source links/visitors, including `<source entry>:wordpress` for WP. Atomic
prune/oldest eviction/register/claim preserves independent clicks; ninth evicts oldest
unclaimed issuance. Consumed rows do not invalidate an already claimed exchange.
Expiry is bounded by ten minutes, entry TTL, source, session and gate window.
Revocation/expiry/restart refuse pending tokens; delayed body/exchange rechecks source.
Completed application sessions retain their own lifetime. Direct CLI singleton state
neither replaces nor authorizes these pooled tokens.

Magic-link capture uses a separate process-local exchange lock shared across magic-link
entries. It bounds lock wait by the upstream timeout and returns HTTP 503
`capture_busy` when busy. The source rechecks gate enablement and source currency after
lock acquisition and after mailbox snapshot, immediately before the application
request. The lock spans snapshot, request, and polling; token pool issue/claim/eviction
uses a separate lock and remains independent of capture I/O. Real Hub token coexistence
and replacement policy are UNVERIFIED by source-only serialization evidence.

## WordPress online claim

Controller/panel WP uses its separate DB-backed eight-hash pool, connection-scoped
advisory lock and cache invalidation. Shared WP uses the gate pool bound to source
entry/link/kid. The per-link plugin validates token/expiry and exact browser Origin
from trusted WP home_url, then claims its nonce using options-table `INSERT IGNORE`
before callback. Main site of main network only; real MySQL/multisite/cache proof pending.

Callback POST at FIXED `claim_origin + /__sandbox/stack/wordpress-claim`:

- Request EXACT `{kid,token_sha256,nonce,at,mac}`. Kid 12/32 lowercase hex, hash/mac
  64 lowercase hex, nonce 32 lowercase hex, at integer Unix seconds within 30 seconds.
- Request HMAC-SHA256 frame is
  `sandbox-share-wp-claim-request.v1|kid|token_sha256|nonce|at`.
- Success EXACT `{ok:true,kid,token_sha256,nonce,at,expires_at,mac}`. Frame is
  `sandbox-share-wp-claim-response.v1|kid|token_sha256|nonce|at|expires_at`.
- Both use decoded `bytes.fromhex(key)`, UTF-8/ASCII literal pipe separators, no
  trailing separator/newline. Gate response at is current integer time; WP checks
  within 30 seconds of its clock AND original request at. Expiry must equal original
  signed token expiry, remain live and not exceed per-link key expiry.
- Token signature remains `sandbox-share-login.v1|kid|exp_decimal|token_nonce` with
  the same decoded key. Gate atomically consumes only a known pending hash and checks
  source/kid liveness, MAC, time and nonce replay before signing. Callback needs HMAC,
  not browser cookies/CSRF; rate limit 120/minute. No stateless fallback.

WP HTTP uses sslverify=true, redirection=0, timeout=5 and response limit 2048;
callback request is bounded 1024 bytes. Reject unsigned, malformed/extra/duplicate,
wrong kid/hash/nonce/MAC/expiry, stale, transport or ambiguous replies before auth.
Nonce is already spent on callback failure; user needs a fresh click. Callback
necessarily carries a bounded TOKEN HASH on wire, never the raw token/key. No hash,
raw token, key or URL enters callback/audit logs. Never describe this as hash-free wire.
Revoke removes per-link WP keys even if gate link is already absent. Independent
source/authentication review and owner release sign-off remain required.

## Panel and compatibility

Panel route `/hosting/<remote>/<slug>` uses recorded identity. Missing target explains
absence. POST `/api/hosting/sign-in` takes exactly `{remote,stack}`, with existing
panel Host/Origin/JSON/body/header guards. One-shot no-store result
`{ok,url,expires_at,one_time,user}` never enters polling. Blank tab opens on click,
navigates on success and closes on safe failure. Recorded CLI target selection for
`sb host login-url --remote R --name S --confirm` never falls back to cwd. No list/GET
mints; server-only panel lacks controller mint authority.

Audit allowlist remains explicit; adding attempt_id requires docs/tests/allowlist
change. No arbitrary exception payload. Controller list/revoke/error envelopes and late wire fields remain pending owner
consolidation; do not invent fields from earlier proposals. Source types alone are not proof.
Public changes need owning docs/tests and revision evidence before supported lifecycle
upgrade and independent installed-revision observation. See ../quickstart.md for the
reviewable acceptance plan. No deployment or live authority is conferred here.

## Worked declaration

See the complete [hosting manifest example](../../../docs/login-service.md#worked-stack-template).
It includes `route_sign_in`, `$wordpress`, `inbox_sign_in` and direct `uses`.
