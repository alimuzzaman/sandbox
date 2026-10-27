# Contract: storage-contract evidence (operator-attested)

Sandbox never connects to the provider database. The operator runs these fixed read-only catalog queries through their own process against the database behind the exact destination and saves the output plus metadata as JSON (0600, controller-local).

```json
{ "kind": "sandbox-ownership-storage-evidence", "version": 1,
  "destination": {"endpoint": "https://manager.example", "projectId": "<project>"},
  "capturedAt": "2026-10-26T10:00:00Z", "expiresAt": "2026-10-26T11:00:00Z",
  "producer": "operator_db_catalog_query",
  "queries": [
    {"id": "index_state", "sql": "<Q1 verbatim>", "output": "<psql -A -t -F'|' output verbatim>"},
    {"id": "v2_selection", "sql": "<Q2 verbatim>", "params": ["<project>"], "output": "<project>|3|t"} ] }
```
Q1 (fixed): `SELECT c.relname, t.relname, i.indisunique, i.indisvalid, i.indisready, i.indislive, pg_get_indexdef(i.indexrelid) FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid JOIN pg_catalog.pg_class t ON t.oid=i.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND t.relnamespace=c.relnamespace AND c.relname='uq_secrets_v2_key_folder_id_shared';`

Q2 (`V2_SELECTION_QUERY`, fixed): `SELECT p.id::text AS project_id, p.version AS version, (p.version = 3) AS should_use_secret_v2_bridge FROM public.projects AS p WHERE p.id = $1;`

The operator's read-only client binds `$1` to exactly the configured project ID;
no ID type cast is assumed. Q2 must return exactly one row matching that ID,
integer version 3, and true (`t` in psql's unaligned text format). Only the
provider's standard public schema is supported by this contract.

Source basis, tag v0.166.5: `secret-v2-bridge-fns.ts:55` selects version 3;
`project-bot-fns.ts:27-37` returns the bridge flag for version 3 when the bot-key
override is not requested; `secret-service.ts:1639` calls getBotKey(projectId)
without that override. Source URLs:
- https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/services/secret-v2-bridge/secret-v2-bridge-fns.ts#L55
- https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/services/project-bot/project-bot-fns.ts#L27-L37
- https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/services/secret/secret-service.ts#L1639


Admission (`admit_evidence`), all required: exact kind/version/keys; destination equals the profile destination (origin and projectId); `capturedAt ≤ now`, `expiresAt > now`, `expiresAt − capturedAt ≤ 3600 s`; producer exact; `sql` strings byte-equal to the shipped constants; Q1 output exactly one row whose fields 1-6 are `uq_secrets_v2_key_folder_id_shared|secrets_v2|t|t|t|t` and whose `pg_get_indexdef` (whitespace-normalized) is `CREATE UNIQUE INDEX`, on `secrets_v2`, columns exactly `(key, "folderId")`, predicate `type = 'shared'` with any `::text`/`::character varying` cast (per migration 20260623144551; the exact canonical string is pinned as `EXPECTED_INDEXDEF` after the disposable-instance proof and until then the cast-tolerant parse applies); Q2 SQL and its single project parameter match the shipped constant and exact destination, and its output satisfies the source-proven predicate. Otherwise `evidence_inadmissible`/`evidence_expired`/`provider_contract_unconfirmed`. Bare booleans, a version string, or source excerpts are rejected by shape.

Labeling: output, approval display and completion record say `storage_attestation: operator_attested`. The word "verified" is never applied to storage. The approval binds `sha256(evidence file bytes)` (not value-derived) and caps approval expiry at `expiresAt`.
