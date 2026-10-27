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

## Proofs still owed

- R4: hidden-value placeholder and exact-name JSON 404 behavior on a disposable provider, with no usable value in existence responses.
- R5: create success, duplicate conflict, approval response, and ambiguous-acceptance behavior, including storage uniqueness under a race.
- R6: exact readback shape and byte equality without transformations or secret-reference expansion.
- R7: derived-token revoke acknowledgment and inability to reuse the revoked token, plus honest behavior on unsupported routes and killed workers.
- Pin the actual catalog `pg_get_indexdef` result after disposable-instance proof.
- Supported synthetic CLI acceptance, focused regressions, and Linux/macOS controller acceptance.
- Human security review of create authority, approval, source continuity, scoped consumers, native credential lifetime, gateway bundle, revocation limits, and CLAUDE.md gotcha 32.

No real source file, credential, existing provider, network, remote controller,
provisioning flow, or production instance was accessed for this implementation.

Integration details: native credential placement routes a fully validated ownership Grant to the separate ownership locator. Linux Secret Service attributes use the locator service namespace. Existence replies carry only exact-grammar controller markers, never arbitrary comments; a foreign marker refuses local adoption and identifies the owning controller. Live native-store placement remains unverified.
