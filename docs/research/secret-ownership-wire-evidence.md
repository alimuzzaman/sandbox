# Secret ownership wire and source evidence

Spec 069 introduces ownership worker protocol version 2. This is a public
protocol change from the spec 067 read-only broker, which keeps its existing
behavior. Ownership uses the separate `sandbox-ownership` credential namespace,
`o1-` authority digests, and CLI-only manifests. No installed controller revision
or live provider compatibility was exercised during implementation. Client and
controller revisions must match before relying on this protocol remotely.

Evidence below comes from the pinned spec contracts and existing source research,
not requests made during implementation. VERIFIED means supported by inspected
source. It does not mean a live provider or Linux controller passed acceptance.
INFERRED means reasoned from that source. UNPROVEN-LIVE names the missing runtime
proof. The source baseline is Infisical tag `v0.166.5`; the storage-index
migration is `20260623144551`.

## Worker protocol revision 2

The parent launches a disposable isolated Python worker with private pipes,
minimal environment, discarded stderr, and its own process group. Frames have a
one-byte type, four-byte big-endian payload size, and bounded payload. `J` carries
canonical ASCII JSON, `V` carries selected raw bytes, and `E` carries a fixed
error code. Unknown, oversized, or out-of-order frames refuse. Full frame grammar
is in [the protocol contract](../../specs/069-secret-profiles/contracts/worker-protocol.md).

The open frame carries protocol version 2, the complete normalized ownership
map, canonical project root, fixed destination/client/capability, exact allowed
names, authority digest, gateway mode, and deadline. Parent and worker each
recompute grant authority before credential access. Capability checks precede
provider requests. One worker session performs one login. Resume can use a
separate read session for exact journal reconciliation before the create session.

Create uses `create` plus a private `V` frame, worker `ready`, parent durable
`attempted` journal append and fsync, then parent `go`. If durable recording
fails, `go` is never released. Read capability may read selected names and
compare expected bytes. Create capability may compare only names attempted in
that session. There is no general provider, list, search, export, update, delete,
or administrative frame. Close attempts derived-token revoke and the parent
always terminates the process group; uncertainty is retained.

Every HTTP request uses `User-Agent: sandbox-secret-ownership/1`, the exact
verified HTTPS origin, no redirects, no ambient proxies, no retries, a bounded
response, and a timeout bounded by destination and session deadlines. Gateway
mode `cloudflare-access` adds only the fixed service-token headers from the
protected three-field credential bundle. This adds no gateway administration,
cookie reuse, or public fallback.

## HTTP operation evidence

| Operation | Fixed request and classification | Evidence |
| --- | --- | --- |
| Login | `POST /api/v1/auth/universal-auth/login`, fixed `clientId` and protected `clientSecret`; require access token and positive expiry | VERIFIED in the existing provider/source baseline; no new live proof |
| Existence | `GET /api/v4/secrets/{name}` with exact project/environment/folder, `type=shared`, `viewSecretValue=false`, references/imports disabled; matching hidden-name metadata means present, JSON 404 means absent, anything ambiguous means unknown | Parameters VERIFIED; hidden response and JSON 404 shape UNPROVEN-LIVE, R4 |
| Policy | No source-confirmed value-free policy endpoint; returns null and relies on response-level approval detection | INFERRED; no claim that change policies are absent |
| Create | `POST /api/v4/secrets/{name}` with project/environment/folder, exact input bytes as text, fixed non-secret controller/run comment, `type=shared`; matching secret means created, approval object means unresolved | Route/body VERIFIED; already-exists 400 recognition INFERRED; timeout/5xx/malformed acknowledgment remains acceptance_unknown, R5 |
| Readback/read | Exact-name `GET` with `viewSecretValue=true` and references/imports disabled; expected bytes compared in RAM, never emitted by readback | Response shape VERIFIED; real endpoint behavior UNPROVEN-LIVE, R6 |
| Revoke | `POST /api/v1/auth/token/revoke` carrying only the derived access token; only 200 with `message` equal to `Successfully revoked access token` means revoked | Route, body, acknowledgment VERIFIED; real deployment route/behavior UNPROVEN-LIVE, R7 |

Provider call details and source labels are pinned in
[the provider contract](../../specs/069-secret-profiles/contracts/provider-infisical-v4.md).
The [v1 auth registration](https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/server/routes/v1/index.ts#L105-L124)
and [access-token revoke route](https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/server/routes/v1/identity-access-token-router.ts#L40-L65)
provide the source basis for the revoke acknowledgment. Revoking this token
never revokes a bootstrap secret, identity membership, native-store entry, or
Cloudflare service token. Those remain operator-managed.

## Storage admission and D1 provenance

The fixed Q1 catalog query checks the live index's relation, uniqueness, valid,
ready, and live flags. It requires the unique shared-secret index on
`secrets_v2` over exactly `key` and `folderId`, with the shared-type predicate.
Sandbox reads an operator-supplied private evidence document; it never connects
to the provider database. Every resulting claim is `operator_attested`.

D1's exact `V2_SELECTION_QUERY` is:

```sql
SELECT p.id::text AS project_id, p.version AS version, (p.version = 3) AS should_use_secret_v2_bridge FROM public.projects AS p WHERE p.id = $1;
```

The parameter is exactly the configured project ID. Admission requires exactly
one result for that project, integer version 3, and true. It supports the
provider's standard public schema only. No project-ID type cast is guessed.
The source chain at `v0.166.5` is
[bridge selection](https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/services/secret-v2-bridge/secret-v2-bridge-fns.ts#L55),
[bot-key flag](https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/services/project-bot/project-bot-fns.ts#L27-L37),
and [secret-service caller](https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/services/secret/secret-service.ts#L1639).
The caller does not request the bot-key override, so version 3 is the
source-supported bridge-selection predicate. See
[the storage contract](../../specs/069-secret-profiles/contracts/storage-evidence.md)
for Q1, exact document grammar, raw-output requirements, and expiry rules.

`EXPECTED_INDEXDEF` remains unpinned pending disposable-instance evidence.
The current validator uses the contract's constrained whitespace/cast-tolerant
parse of the exact index identity, btree columns, and shared predicate. A source
constant being present is not live storage proof. Approval expiry is capped by
the evidence window, which is at most one hour.

## Disposable-provider observation (2026-10-27)

Infisical v0.166.5 with synthetic Postgres and Redis ran as a throwaway Compose
instance on the xcloud-london remote, with no host port and no public route. A
script inside the container exercised it over plain HTTP on loopback with
synthetic identities and one synthetic item. This shows wire shapes only. HTTPS,
certificate trust and the Sandbox client path were not exercised.

- R4 observed: existing item with `viewSecretValue=false` returned 200 with a `secret` object; the value was not the real value and not empty; the item with a missing name returned a JSON 404 `not found`.
- R5 partly observed: create returned 200 with `secret`; a duplicate create returned 400 with an "already exists" message. The approval-policy response, ambiguous acceptance and the storage race were not exercised.
- R6 observed: exact-name read with `viewSecretValue=true` and references and imports disabled returned the stored bytes unchanged.
- R7 observed: `POST /api/v1/auth/token/revoke` returned 200 `Successfully revoked access token`; reuse of that token returned 401 "token has been revoked".
- A Viewer-role identity could read the item with the value hidden.

The instance was deleted afterwards; its leftover remote checkout is released for the regular reaper.

### Sandbox client over HTTPS (2026-10-27)

The same release then ran as a hosted environment behind the xcloud-london nginx
front door at a disposable proxied hostname, with Cloudflare strict TLS and an
Origin CA certificate. A synthetic organization, project, Viewer identity and
item were created over HTTPS. The Viewer client secret went to the macOS Keychain
only through `secrets credential-set --create-only --stdin`.

- `source-info`: `reachable`, `provider_state: available`, with no secret read.
- `inspect --mode keys`: only the configured singleton.
- Fixed `use-profile`: Universal Auth login, exact-item read and delivery to the fixed child (`present=true`), exit 0. No value appeared in output.
- An arbitrary `secrets run` against the provider source was refused with `command_denied`.
- Changing one source field made the credential unreachable (`credential_missing`); restoring the configuration restored it.

Two defects found on this path were fixed with tests. First, the provider
transport now sends a fixed `User-Agent`, because Cloudflare's browser integrity
check refused urllib's default agent with 403, reported as `provider_denied`.
Second, `host apply` now waits once before verifying a newly created DNS record.
Otherwise a resolver can cache NXDOMAIN for the zone's negative TTL, and the
apply fails and rolls back.

This covers the spec 067 read path only. The ownership `migrate` path needs the
human-only `approve` step and was not run.

### Ownership worker over HTTPS (2026-10-27)

The hosted instance was reset to a fresh database. A synthetic project got a
Viewer read identity and a Member create identity; a Member can also edit and
delete, so a create-only custom role was not exercised. Both client secrets went
to the Keychain only through `ownership credential-set --create-only --stdin`.
The source was a synthetic 0600 dotenv file with two entries.

- `ownership classify --check-destination`: Universal Auth login, value-free existence over HTTPS, both names `would_create`, revocation `revoked`, exit 0 (three runs).
- A read-only sidecar ran the fixed Q1 and Q2 against the provider database. `ownership evidence-check` admitted the resulting document as `operator_attested`.
- Observed `pg_get_indexdef` on Postgres 14: `CREATE UNIQUE INDEX uq_secrets_v2_key_folder_id_shared ON public.secrets_v2 USING btree (key, "folderId") WHERE ((type)::text = 'shared'::text)`. It is not pinned as `EXPECTED_INDEXDEF`: the cast-tolerant parse admits it, and an exact pin from one Postgres version could refuse valid evidence from another.

One defect was found on this path and fixed with tests. The revoke itself
succeeded, but process-group cleanup reported the worker as not ended, so every
session reported `cleanup_unknown`. Darwin answers `killpg(pgid, 0)` with EPERM
while the group leader is exiting or is an unreaped zombie. Cleanup now treats
that as our own child still present and lets the bounded wait reap it. EPERM
counts as unreachable only once the leader is reaped. The spec 067 broker and the
scoped runner share this helper.

## Proofs still owed

- R4: the exact hidden-value flag and placeholder shape were not captured (the output filter redacted the flag); confirm against the Sandbox client.
- R5: approval-policy response, ambiguous-acceptance behavior and storage uniqueness under a race.
- R7: honest behavior on unsupported routes and killed workers.
- HTTPS path: ownership create and readback through the Sandbox client after a human `approve`. Login, existence and revoke are observed above.
- Decide whether to pin `EXPECTED_INDEXDEF` to the Postgres 14 string above, given other provider Postgres versions.
- Supported synthetic CLI acceptance, focused regressions, and Linux/macOS controller acceptance.
- Human security review of create authority, approval, source continuity, scoped consumers, native credential lifetime, gateway bundle, revocation limits, and CLAUDE.md gotcha 32.

No real source file, credential, existing provider, network, remote controller,
provisioning flow, or production instance was accessed for this implementation.

Integration details: native credential placement routes a fully validated ownership Grant to the separate ownership locator. Linux Secret Service attributes use the locator service namespace. Existence replies carry only exact-grammar controller markers, never arbitrary comments; a foreign marker refuses local adoption and identifies the owning controller. Live native-store placement remains unverified.
