# PostgreSQL restore proof coverage

This contract describes what the existing PostgreSQL recovery observation checks.
It does not expand a receipt's meaning or mark an unrun restore as verified.
The source baseline is Sandbox `eec81ed`. The separate archive-bound reference
correction is still owned by the recovery task, based on `ef5a239`; its dirty
implementation and runtime acceptance have not been accepted into this branch.

## Meaning of a verified receipt

A verified receipt proves the recorded isolated checkpoint under its exact source,
archive, dump, image, target and request bindings, subject to the fields below.
It does not prove later writes, application readiness, deployment, or every
property that PostgreSQL can restore. A schema or data item may be present in the
dump and restored by PostgreSQL without being independently checked by this
receipt.

The existing raw schema digest covers the public-schema column/constraint
projection in [`_SCHEMA_FIELDS_SQL`](../sandbox/recovery/postgres_helper.py).
The separate proposed version-2 reference mode keeps that original digest and
adds a structural digest. When permitted, it compares the target projection to
an independent schema-only restore of the exact dump with the exact pinned
image. That is an exact native-restore comparison of the selected projection;
it is not a general SQL-equivalence or full-schema proof.

## Coverage boundary

| Property | Receipt guarantee when all required checks pass | Outside that guarantee |
|---|---|---|
| PostgreSQL version | Recorded server major matches | Full server settings or arbitrary cross-version equivalence |
| Database identity | Recorded database-name hash matches where required; renamed isolated destinations use the explicit existing exception | Cluster identity or unrelated databases |
| Public table inventory | Observed table names and per-table counts match | Row values, order, or complete table properties |
| Public column identity | Table/column names and within-table order match | Unselected catalog fields |
| Public column nullability | Recorded `is_nullable` matches | Other column constraints outside the selected projection |
| Column type | Recorded `data_type` matches | Type modifiers, precision/scale/length, domains, full user-defined/array type identity |
| Constraint identity | Public constraint table/name and validation state match | Unselected constraint/index metadata |
| Constraint definition | Raw projected text matches; proposed reference mode instead requires the bound independent restored projection plus captured structural checks | Formal SQL semantic equivalence across versions |
| Primary/unique constraints | Their selected `pg_constraint` fields are compared | Complete supporting-index definition, options or storage properties |
| Migration history | The recorded migration-name/checksum/finished/rolled-back projection matches | All migration IDs, timestamps, logs or metadata |
| Defaults, generated expressions, identity options | No independent comparison | These properties are outside the current observation contract |
| Collations, standalone indexes, sequences | No independent comparison | Definitions, ownership, sequence options and current values |
| Triggers, functions, procedures, extensions | No independent comparison | Definitions, bindings, extension versions or behavior |
| Non-public schemas | No independent comparison | Tables, data and schema objects outside `public` |
| Ownership and grants | No preservation claim; restore deliberately uses `--no-owner --no-acl` | Source ownership, grants and ACL equivalence |
| Row-level security | No independent comparison | Enablement, force flags and policy definitions |
| Row content | No full-content equality claim | Equal table counts can hide different values |

Synthetic sentinel rows in an integration canary prove those fixture rows only.
They do not upgrade the receipt to a general content-integrity guarantee.
Networkless reference isolation contains restore execution; it does not add
comparison coverage for functions or other executable schema objects.

## Version and acceptance rules

- Older archives and receipts keep their original fingerprints, identities and
  comparison meaning. Do not relabel them as reference-mode evidence.
- Accept the separate reference correction only after its exact candidate,
  archive/image bindings, negative controls, original-target preservation,
  owned-reference cleanup, terminal canary results and requested retained-target
  verification are observed. Mocked tests alone are insufficient.
- The proposed legacy-reference exception is limited to its reviewed local
  `lenzora-dev` drill and exact unchanged bound source. It does not authorize
  production, external credentials, another source or an archive rewrite.
- A project that requires proof of omitted fields needs a separately specified,
  versioned observation/comparison contract and acceptance cases. Do not silently
  add fields to the old digest or infer coverage from a successful `pg_restore`.
- Broadening schema or row-content guarantees is a separate material feature,
  following Spec Kit. This coverage document records the present boundary and
  does not authorize that implementation or any protected restore.

See the [delivery completion record](delivery-repair-completion.md) for owner
integration and observed acceptance status. Until those gates are met, the new
reference mode remains an unaccepted candidate.
