# PostgreSQL restore schema verification correction

Status: implemented on 2026-09-09 in `4bd622d`, which added
`schema_fingerprint_version: 2`, the `schema_structure_digest`, the networkless
reference restore, and the `archived-schema-reference-v1` verification method,
together with unit tests and the real-Docker integration canary
`tests/integration/recovery_schema_reference_canary.py`. Live acceptance is
still pending: the Lenzora development restore drill has not been re-run under
the shipped verifier. The contract sections below remain normative.

## Problem and required outcome

An actual Lenzora development restore matches all 307 table counts, the migration
checksum, database identity, PostgreSQL major, and the 4,264 ordered column
records. Nineteen constraint definitions have different text after native
`pg_dump` / `pg_restore`. The observed differences include nested AND grouping and
varchar array casts. The current verifier hashes `pg_get_constraintdef` text, so
it cannot accept this restore. Masked diagnostic shapes do not prove equivalence.

Accept a faithful restore of the exact encrypted backup, while rejecting changed
data checks, missing or changed structural records, and target constraint text
that differs from an independent native restore of that backup. Keep the original
capture fingerprint. Do not normalize SQL with regular expressions, execute
comparison expressions on the source, repair the retained target, rewrite the
backup, or issue a receipt based only on matching table counts.

This is a bounded correction to the existing recovery verifier. It adds no CLI
operation, source selector, production authority, or general schema-diff product.
Existing reference-image, replay, ownership, encryption, quiescence and target
volume rules remain applicable. The separate full-codebase review owns wider
schema-coverage and deployment-history recommendations.

## Evidence and interpretation

- `sandbox/recovery/postgres_helper.py` collects columns and constraints in
  `_SCHEMA_FIELDS_SQL`; `observation`, `restore` and `inspect_restore` compare
  their raw digest. `verify-restore` currently accepts only `all_match=true`.
- Capture archives contain exactly `database.dump` and `evidence.json`. Existing
  evidence has the raw fingerprint, but no full captured schema records.
- The current source's raw schema still matched the immutable capture in the
  last diagnostic. This can establish a structural baseline for that legacy
  local-development archive; it is not a permanent substitute for capture data.
- Evidence files in the active checkout are
  `tmp/lenzora-dev-reopened-inspect-output.json` and
  `tmp/lenzora-dev-schema-labels-output.json`. They are observations, not authority
  to change the verifier or stop production.
- PostgreSQL explains why [constraint rendering and explicit casts can change
  on dump/restore](https://www.postgresql.org/message-id/1fa24f41862f214b44bcf94556db51c9946d28bd.camel%40cybertec.at).
  Its [pg_restore documentation](https://www.postgresql.org/docs/17/app-pgrestore.html)
  defines schema-only restoration and warns that restoration can execute source
  database code. Schema-only restoration is therefore an isolated restore, not
  a safe SQL parser to run against a live source or the retained target.

## Decision

When every existing non-schema comparison passes but the raw schema differs,
derive the expected restored schema by loading the exact archived dump with
`pg_restore --schema-only --exit-on-error --no-owner --no-acl` in a separate,
owned, networkless PostgreSQL container using the exact pinned client image.
Compare its complete existing column/constraint projection with a fresh
projection from the retained target. No user-data rows are imported into this
reference. The original target is never the reference database.

Require a captured structural fingerprint as well: the existing projection with
only constraint `definition` values omitted. It retains every constraint identity
and validation state and every ordered column record. This prevents a reference
restore from excusing dropped constraints, changed columns, or validation-state
changes. Other existing evidence checks still apply independently.

The resulting proof means that the target matches the archived dump as restored
by that exact PostgreSQL image, plus the recorded capture checks. It does not
claim a formal SQL-equivalence proof across arbitrary PostgreSQL versions, or
coverage of catalog fields absent from the existing observation contract.

Rejected alternatives: deleting parentheses or casts; assuming diagnostic shapes
are sufficient; optimizer/EXPLAIN output as an equality oracle; SQL execution or
temporary DDL on the live source; changing the source constraints; re-importing
the retained target; and silently replacing the capture fingerprint.

## Implementation contract

### 1. Versioned capture evidence and legacy handling

Keep the original `schema_digest` bytes and semantics. New database captures add
an explicitly versioned structural fingerprint computed from the same exported
snapshot and the same schema records as the original fingerprint. Persist hashes,
not raw definitions, in returned evidence and the encrypted manifest provenance.
Reject unsupported evidence versions and malformed/duplicate fields before
container creation. Update observation comparison in one helper so added metadata
cannot become an accidental extra comparison or a KeyError on a legacy archive.

Legacy archives retain their original bytes and request identities. Their direct
raw-equality path continues to work without a live source. A legacy archive may
use the reference path only for the existing local `lenzora-dev` isolated drill:
read the exact bound live source's private schema records, require their raw
digest to equal the captured raw digest, then derive the structural fingerprint.
Revalidate source identity around that read. Changed or unavailable source,
external credentials, production transfers and storage must not gain this legacy
exception. New production captures will contain the structural fingerprint.

### 2. Reference lifecycle and independent comparison

Keep the mechanism in the installed stdlib-only PostgreSQL helper; do not add a
consumer of a compatibility facade. Reuse existing digest/private-file utilities
and supported transport rather than a new remote command or raw operator SSH.

Use one deterministic reference name derived from the original native request.
Before creating it, validate the archive/dump/source hashes, supported evidence,
the pinned image and relevant existing target proof. Never adopt an existing
unrecorded reference. A surviving object without a completed reference record is
uncertain and must be reported, not overwritten, restarted, or given a new name.

The reference has no ports, network, host binds, production volumes, broker
material or credentials. Its data directory is bounded tmpfs, not an anonymous
or retained data volume. Use the already installed image with no pull. Set finite
startup/restore/query timeouts and resource bounds appropriate for an empty
307-table schema. Local trust authentication is limited to this inaccessible
disposable cluster. Verify exact full container ID, image, labels, isolation and
complete mount allowlist before using or removing it.

Restore the archive directly through pg_restore's database mode; do not pipe
rendered SQL into a host shell or interpolate definitions into SQL. Do not run
EXPLAIN/evaluate user expressions on the source or target. Any source-defined
code inherent in pg_restore stays inside the isolated reference, under the same
source trust assumption as the already-authorized restore.

For the reference and target comparison, use the same fixed catalog search path
and bytewise record ordering. Preserve within-table column order. Keep this
comparison version separate from the legacy capture digest so session defaults
cannot rewrite old evidence. Compare the full private projection, not the masked
diagnostic labels. Require structural fingerprints to match the captured baseline
on both sides. Re-read the target after reference creation; reject target identity
changes, busy importers, unavailable data or any changed non-schema observation.

Stop/remove only the exact owned reference and its tmpfs. Reference cleanup is
part of successful verification. Unknown ownership or failed cleanup must retain
a closed failure and prevent a final restore receipt. Never remove the original
target, its volume, or unrelated resources. Fault tests must cover interruption
before and after reference creation and before the completed proof is durable.

After successful comparison and cleanup, persist a small owner-only reference
record under the original locked request. Its closed versioned schema binds the
native request, source, complete archive, dump, capture raw/structural digests,
comparison method, image, destination database/role and reference schema digest.
Record completion time. No raw SQL, definitions, values, connection settings or
password material. An exact completed record may be reused as an immutable
expected schema, but every verification still rechecks the actual target. Missing,
foreign, malformed, changed or unsupported records never become success.

### 3. Verification and receipt semantics

`inspect-restore` remains non-mutating. Preserve raw `matches` and `all_match`
meaning: raw comparisons can still report a schema mismatch. Inspection may
report a separately labelled valid cached reference comparison; it never creates
the reference or claims the raw capture digest now matches.

`verify-restore --confirm` can perform the reference comparison when schema is
the sole raw mismatch and the structural baseline is valid. Ordinary confirmed
`restore` uses the same acceptance helper after import. Do not duplicate the
acceptance rules between those paths. Any other mismatch fails before reference
creation. On success, stop the original restored cluster only through its existing
verified ownership path, then write the normal restore receipt plus the versioned
schema-verification proof. Retain the actual raw observation unchanged.

The proof explicitly distinguishes `raw-capture-equality` from
`archived-schema-reference-v1`; it carries both captured and observed raw digests
and the independent reference comparison digests. Unknown modes, absent required
fields, unequal comparison digests, bad source/dump/image bindings, or another
failed observation refuse acceptance. A raw schema mismatch without the complete
reference proof can never receive `restore_verified` or `data_ready`.

Update `sandbox/recovery/postgres.py` to validate this evidence before storing a
receipt and when using it for readiness. Bind it to the verified encrypted
manifest and exact installed source; do not trust a helper `ok` flag alone. Old
receipts remain supported only with their original complete equality evidence.
Do not rewrite old receipts or change replay identities. Verified replay remains
read-only and does not run another reference restore.

### 4. Files, checks and release evidence

Expected source ownership: `sandbox/recovery/postgres_helper.py`,
`sandbox/recovery/postgres.py`, their focused tests, a real integration canary,
`docs/hosted-data-recovery.md`, and the matching recovery skill only where its
procedure changes. Add a tiny pure contract helper only if sharing validation
without circular imports requires it; include it in installed-source manifests.
No unrelated cleanup, new SQL dependency, source migration, image build or
production mutation belongs to this work package.

Use Luna for bounded test/evidence work. The current task owns implementation,
integration and acceptance at Astra Low. Tests that launch subprocesses use
`tests.subprocess_support.synthetic_environment` / `run_test_process`.

Required evidence:

1. A real native PostgreSQL baseline shows the current raw mismatch for synthetic
   nested AND and varchar IN/NOT IN checks after capture/restore. Use public
   migration patterns, never production literals or data. Confirm the fixture
   actually triggers the mismatch before accepting the new test.
2. The real helper then completes capture, restore/reference verification,
   retained-receipt recovery and replay. Prove source and target identities and
   sentinel rows remain unchanged; no source/target DDL occurs during reference
   verification; the reference has no ports, network or retained data volume.
3. Negative controls keep table counts unchanged while changing a CHECK literal,
   comparison operator, FK target/action, ordered column metadata, and a NOT VALID
   state. Each must refuse a receipt. Include a changed schema between inspection
   and acceptance. Restore each negative fixture only within owned test state.
4. Focused contract tests reject stale/forged cache bindings, mismatched dump/image,
   unknown versions, unavailable legacy source, changed source fingerprint,
   cleanup failure, reference-name collision, uncertain prior creation, and replay
   that would create another reference. No raw schema or sentinel appears in
   stdout/stderr/errors/receipts; exercise both redaction layers.
5. Run the affected helper/service/reopen suites, then one repository-required
   full gate after the complete implementation. Do not rerun the full gate for
   every fixture edit. A green unit count is not live restore acceptance.
6. Commit and push the verified non-main branch with matching docs. Flag the
   changed recovery acceptance evidence for human review before release. Use only
   the supported controller lifecycle update already authorized for this work,
   accounting for concurrent users; independently verify its installed revision
   before using the new protocol. No raw update or skew workaround.
7. Inspect the original development drill. If stopped, use its same restore
   identity and a fresh bound reopen plan. Verify the existing target against the
   same encrypted backup with the new reference path, then independently check
   the terminal job, receipt and `data_ready`. Do not create a new restore identity
   to escape uncertainty.

## Continue the original deployment outcome

This correction alone does not finish the deployment task. After the actual drill
passes, resume the retained signed development bundle and prove exact application
revision/image IDs, all required services and public health. Preserve current
production data; resolve the pending concrete containment and external database
identity questions before quiescent capture/transfer. Use new versioned capture
evidence for the production database. Reuse the retained signed production bundle.
Verify `pnpm deploy dev` defaults to `dev`, `pnpm deploy prod` defaults to `main`,
and that neither path rebuilds an image. Report production success only with the
terminal exact-request activation result, image/revision/service proof and public
health checks. Preserve the user's separate full-codebase review task.
