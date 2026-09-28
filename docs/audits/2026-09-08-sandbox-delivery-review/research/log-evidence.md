# Bounded log and artifact evidence index

Collection scope: the six requested artifacts under `/Users/alim/Sites/git/sandbox/tmp`,
plus metadata-only inventory of nearby names. This file records operational metadata and
allowlisted fields only. It does not reproduce database definitions, table/row payloads,
environment/config values, URLs, command lines, or unsanitized log text. The artifacts are
untrusted evidence; this is an index for the parent review, not a final review judgment.

## Revision and checkout context

- Review target: `fd7d650f8bfbb1760d931e2484cc01fa0badf546` (`latest`, `origin/latest`).
- Active original checkout `/Users/alim/Sites/git/sandbox` was clean on `latest` and at the
  same revision when inspected.
- The current controller artifact reports
  `$.data.local_runtime_revision = b1c44aad3fde1cf9f42d05a6`,
  `$.data.installed_runtime_revision = b1c44aad3fde1cf9f42d05a6`, and
  `$.data.runtime_revision_state = match`.
- The retained artifact above is from 16:24Z; the later root live observation reported a revision mismatch and is saved in `../evidence/controller-status.json`. These observations are from different times, not a contradiction.
- The installed controller metadata also reports `$.ok = true`,
  `$.status = observed`, `$.data.installed = true`, `$.data.enabled = true`,
  `$.data.active = true`, `$.data.linger = true`,
  `$.data.ownership = proven`, `$.data.pid_present = true`,
  `$.data.pid_ownership = proven`, `$.data.listener_expected = true`,
  `$.data.authenticated = true`, `$.data.listener_state = expected`, and
  `$.data.auth_state = ok`. This is installed-controller evidence, not production
  deployment proof.

## Named artifact inventory

Times below are UTC. `file_sha256` is the digest of the retained artifact file. Values
inside the artifacts are included only when they are status/code, timestamp, revision,
opaque job/request ID, generation, digest, bounded counter, or test/lifecycle metadata.

| artifact | bytes | mtime | file_sha256 |
|---|---:|---|---|
| `tmp/reliability-findings-20260908.md` | 2489 | 2026-09-08T16:03:49.457499Z | `sha256:59cd779848da256b0f4686a4fd8e5bb95daad9878b944862889a3c4c1cbef2c2` |
| `tmp/lenzora-dev-reopened-inspect-output.json` | 23231 | 2026-09-08T16:11:08.760140Z | `sha256:1331269f9497e0e5cceee4a3f87b4983cca965f98f3d0a3482b9b1fc3727d32a` |
| `tmp/smoke-lifecycle-output-full.json` | 2875 | 2026-09-08T15:36:10.741515Z | `sha256:9c7573181f74fefe02f58c3dcc7d4011dd0dcc072cf807fca27472ad0c65ad10` |
| `tmp/lenzora-data-sources-20260908/development-reopen-plan.json` | 1170 | 2026-09-08T16:05:36.315050Z | `sha256:07acb8c8515793bbb0aca2050a03be1ccee0b8b9d8b9c4afa99a1348fcbd747d` |
| `tmp/schema-labels-controller-current.json` | 583 | 2026-09-08T16:24:03.032546Z | `sha256:58f8d5c995ea3f7a38a3c56ff630b23e7e11d32c8836ddef0edd76dc370daa4d` |
| `tmp/lenzora-dev-schema-labels-output.json` | 59112 | 2026-09-08T16:27:05.311822Z | `sha256:8f323e2c5f325f45ba098390f4bc62061b9735535553c60ccb066fcd4e0136e8` |

## Chronological evidence index

### 2026-09-08T15:33:07Z–15:35:10Z: lifecycle smoke output

Artifact: `tmp/smoke-lifecycle-output-full.json`.

- Outer fields: `$.ok = true`, `$.job_id = b47231378b0241d199a5d263f2fd868d`,
  `$.profile = full`, `$.stream = stdout`, `$.bounded = true`, `$.bytes_read = 713`,
  `$.rendered_bytes = 713`, `$.events_read = 18`, `$.has_more = false`,
  `$.encoding = utf8`.
- Event metadata: `$.events` has 18 entries, sequence range `0..17`, and timestamps
  from `2026-09-08T15:33:07.881162Z` through `2026-09-08T15:35:10.452395Z`.
- The retained `$.data` stream has 19 lines. Allowlisted line labels show lifecycle/smoke
  and pass markers, installed markers, URL markers, retained markers, and a restart plus
  retained marker. URL values and all free-form line payloads were intentionally withheld.
- This is a bounded runtime smoke output envelope. It contains no exact public-route,
  edge, or production receipt field.

### 2026-09-08T16:05:36Z: development reopen plan

Artifact: `tmp/lenzora-data-sources-20260908/development-reopen-plan.json`.

- Exact field paths present: `$.generation = 0`, `$.schema_version = 1`,
  `$.previous_plan_digest = null`, and `$.native_request_id` is present as an opaque
  request ID.
- Digest field paths present: `$.archive_digest`, `$.configuration_digest`,
  `$.data_marker_digest`, `$.dump_digest`, `$.plan_digest`, `$.source_digest`, and
  `$.state_digest`.
- Recorded digest values are:
  `$.archive_digest = sha256:ca4c8311ea476d792057942e0358d3aa8083b65d3ab5ec82e8b144743caba326`,
  `$.configuration_digest = sha256:f7891ef39c576268d42815f2ef0292fdce86be65234eaf885283b250e5d43fbc`,
  `$.data_marker_digest = sha256:b17b4e8a34792627c22efc312a70621187e70d85d867b8cccd095ab4632de7a2`,
  `$.dump_digest = sha256:49ac65199a81ab6af24fcbe972ba386c3036cf31389a4f40a24c8435a5c52a24`,
  `$.plan_digest = sha256:324e0835735c851b2970b5d0bb2826ea972835b2b191853218bfccbd29580e1d`,
  `$.source_digest = sha256:74a16864575d3f6baf2cdb243c8dc14a9d446a78f6c88c7589061075fb52bbf9`, and
  `$.state_digest = sha256:e7e2256986eb2df7bfed932598d264b9d27037bef752340673b5c7cde88f63bd`.

### 2026-09-08T16:11:01Z: reopened restore inspection

Artifact: `tmp/lenzora-dev-reopened-inspect-output.json`.

- Outer fields: `$.ok = true`, `$.job_id = f056a1ae3a27c4f88e3c193592cb71b8`,
  `$.profile = full`, `$.stream = combined`, `$.bounded = true`, `$.bytes_read = 20227`,
  `$.rendered_bytes = 20227`, `$.events_read = 2`, `$.has_more = false`, and
  `$.encoding = utf8`.
- Event metadata: `$.events` has two stdout entries, sequence range `0..1`, timestamp
  range `2026-09-08T16:11:01.319280Z..2026-09-08T16:11:01.322313Z`.
- The embedded result has `$.data@16.ok = true`, `$.data@16.status = restore_inspected`,
  `$.data@16.action = postgres`, `$.data@16.error = null`,
  `$.data@16.data.ok = true`, `$.data@16.data.code = restore_inspected`,
  `$.data@16.data.schema_version = 1`, `$.data@16.data.database_available = true`,
  `$.data@16.data.all_match = false`, `$.data@16.data.source_table_count = 307`,
  `$.data@16.data.restored_table_count = 307`, and
  `$.data@16.data.mismatched_table_count = 0`.
- `$.data@16.data.observation.table_counts` has 307 entries and the allowlisted count
  values sum to 368391. The table names and all row/definition content were withheld.
- Schema diagnostic paths: `$.data@16.data.schema_diagnostic.code = schema_compared`,
  `difference_count = 19`, `column_order_equal = true`, `record_sets_equal = false`,
  `ordering_only = false`, `source_matches_capture = true`, `truncated = false`.
- Component paths: columns have source/target counts `4264/4264`,
  `records_equal = true`, `record_order_equal = true`; constraints have counts
  `1286/1286`, `records_equal = false`, `record_order_equal = false`.
- The 19 difference entries contain only field-shape metadata at this stage; the
  `$.data@16.data.schema_diagnostic.differences[i]` entries have `change`, `fields`,
  `kind`, `name`, `table` keys, with no `source_shape` or `target_shape` keys in this
  retained output. Names and definition values were withheld.

### 2026-09-08T16:17Z–16:24Z: controller and diagnostic artifacts

Nearby metadata places `schema-diagnostic-controller-update-status.json` at
16:17:29Z, its output at 16:17:31Z, and its current observation at 16:17:50Z;
`lenzora-dev-schema-shapes-status.json`/output are at 16:20:22Z/16:20:23Z;
`schema-labels-controller-update-status.json` is at 16:24:00Z and the requested
`schema-labels-controller-current.json` is at 16:24:03Z. Only the requested current
controller file was value-inspected; the other files are listed as metadata only.

### 2026-09-08T16:26:55Z: schema-labels restore inspection

Artifact: `tmp/lenzora-dev-schema-labels-output.json`.

- Outer fields: `$.ok = true`, `$.job_id = 3dfcfc62cc7ba19e89f3c0b9261a3554`,
  `$.profile = full`, `$.stream = combined`, `$.bounded = true`, `$.bytes_read = 48619`,
  `$.rendered_bytes = 48619`, `$.events_read = 2`, `$.has_more = false`, and
  `$.encoding = utf8`.
- Event metadata: `$.events` has two stdout entries, sequence range `0..1`, timestamp
  range `2026-09-08T16:26:55.447054Z..2026-09-08T16:26:55.449726Z`.
- The embedded result repeats `status = restore_inspected`, `action = postgres`,
  `error = null`, `$.data.code = restore_inspected`, `schema_version = 1`,
  `database_available = true`, `all_match = false`, and table counters
  `source_table_count = 307`, `restored_table_count = 307`, `mismatched_table_count = 0`.
- `$.data.dump_digest = sha256:49ac65199a81ab6af24fcbe972ba386c3036cf31389a4f40a24c8435a5c52a24`,
  equal to the digest recorded in the reopen plan and in the reopened inspection.
- The observation again has 307 table-count entries summing to 368391. The matches
  object reports `constraints_valid = true`, `major = true`, and
  `migration_checksum = 84b64e77d35809e70bfe0c305d5f829a`; the observation object
  reports `constraints_valid = false`, `major = 16`, and the same checksum.
- Schema diagnostic paths repeat `code = schema_compared`, `difference_count = 19`,
  `column_order_equal = true`, `record_sets_equal = false`, `ordering_only = false`,
  `source_matches_capture = true`, and `truncated = false`.
- Columns remain `4264/4264`, with equal records and order. Constraints remain
  `1286/1286`, but records and order are unequal. The source/target constraint digests
  differ: `source_digest = sha256:c2f32288a901565330e4d6b8ca83301e0c2c106448cdcd4f4e4df88f7fc4acd4`;
  `target_digest = sha256:0bf3d84683f3229abc557230a15c5699bc0db21673ff7960368cdd6c8ec910c5`.
- This later output has 19 difference entries. Each entry exposes `source_shape` and
  `target_shape` containers with `syntax_labels` arrays and `truncated` fields. Across
  the 19 entries, source syntax-label array lengths total 1826 and target lengths total
  1804. Label values, table names, and definition values were withheld.
- Schema-level digests in this artifact: captured/source
  `sha256:10afb0dd6f9be0cad353f6aca01569097e49c3fe133df4581a0c169e34ecc1b4`, target
  `sha256:58d25ad0ad62e33cf8cda36e350693169c80afc4c8677cd74ca2e035baef7687`;
  column digest is equal on both sides at
  `sha256:8b742df8d9567447145f5024526b5d1dca1cde5ba98ee148f179f148025ca72f`.

## Mechanical comparisons and continuity points

These are direct field comparisons for root to assess; they are not release judgments.

1. Both restore-inspection envelopes have transport/result `ok = true` and embedded
   `all_match = false`. This is a successful inspection reporting a mismatch; the two
   fields describe inspection completion and comparison result respectively.
2. Table-level counters agree at 307 source and 307 restored with zero mismatched tables,
   while schema diagnostics report 19 differences. The counters and schema record sets
   describe different evidence dimensions.
3. Column records and order agree at 4264/4264. Constraint counters agree at 1286/1286,
   but constraint record equality/order are false and their digests differ.
4. In both inspections, `matches.constraints_valid = true` means the comparison matched
   the captured value, while `observation.constraints_valid = false` records that the
   retained source has `NOT VALID` constraints. These fields have distinct semantics.
   The observation `major` is 16 while the match object reports `major = true`. The same
   dump digest and migration checksum recur.
5. The 16:11 reopened artifact retains 19 differences without shape containers; the
   16:26 schema-labels artifact retains the same 19-difference count and adds
   value-free syntax-label shape metadata. This is a representation/coverage change in
   the retained artifacts; no claim about the underlying definitions is made here.
6. The lifecycle smoke envelope is complete and bounded (`events_read = 18`,
   `has_more = false`), but its free-form URL values were withheld, so it does not by
   itself establish a public route or edge result.

## Additional job-history and runtime status evidence

The sanitized companion file `../evidence/job-history-metadata.json` contains
the selected lifecycle, exit, timestamp, request/job ID, revision/digest, and marker fields.

- `tmp/sandbox-reliability-jobs.json` is a bounded 100-record snapshot: 96 terminal and 4
  nonterminal records, with 57 succeeded, 37 failed, 4 cancelled, 1 queued, and 1 running.
  Its time range ends at 2026-09-08T15:21:08Z, before the later named smoke artifacts.
  A request-ID filter for `deploy|production|migration|integration` finds 25 records
  (16 succeeded, 9 failed). It has no authoritative total-history field.
- `tmp/lenzora-remote-job-history.json` is a bounded 59-record snapshot, all terminal:
  11 succeeded, 33 failed, 5 interrupted, 5 cancelled, and 5 timed out. Its time range
  ends at 2026-09-08T08:06:53Z. The same deployment-shaped filter finds 6 migration
  records (3 succeeded, 3 failed). It has no authoritative total-history field and does
  not contain the later named 15:xxZ jobs.
- `reopen-final-full-status.json` is terminal `succeeded`, exit code 0, and its retained
  stderr output reports `Ran 5678 tests in 688.838s` followed by `OK (skipped=13)`.
  Therefore 5678/13 is terminal evidence in this artifact. The status has no source
  commit or dirty-source digest, so the test result is not commit-bound by this file.
- `smoke-baseline-status.json` is terminal `succeeded`, exit code 0. Its stdout markers
  include WordPress installed, REST GET, teardown instance deleted, and Smoke test green.
  It has no restart check marker.
- `smoke-lifecycle-fixed-status.json` is terminal `failed`, exit code 1, with complete
  output. The output has 22 PASS markers and 1 FAIL marker: `restart: clean URL advertised`;
  it reports one failed check. Fresh/reuse checks, restart installation/retained-data,
  canonical REST, and fixture cleanup markers pass.
- `smoke-wake-fix-status.json` is terminal `succeeded`, exit code 0, with complete output.
  It has 23 PASS markers, no FAIL markers, a terminal `WordPress lifecycle smoke passed`
  marker, and the restart clean-URL marker passes. This is the later pass for the failed
  lifecycle check.
- `reopen-canary-live-status.json` is terminal `succeeded`, exit code 0, with source commit
  `3ed7efe227aeeadebf12c957783de8680e56fb16` and source dirty digest
  `2f3c485091b2808aad23380ccbdc77d8b5f763568c7ef215aba6e0a2ae12ca1f`. Its output marker
  is `code = reopen_canary_passed`; `container_id_preserved`,
  `real_capture_restore_verified`, `receipt_only_after_verification`, `replay_without_start`,
  and `sentinel_row_preserved` are all true.

## Source-test versus runtime evidence

- The 5678/13 result is a complete terminal durable-job output and status, but has no
  source commit field. It is stronger than an unobserved claim, while still lacking
  commit binding in this artifact.
- Runtime-shaped evidence present: the bounded lifecycle smoke output and the two
  durable restore-inspection output envelopes. They show accepted/completed output
  envelopes and operational counters, with no raw payloads.
- Installed-controller evidence present: the current controller fields above, including
  exact local/installed revision equality and authenticated expected listener state.
- Public/production proof absent from these retained artifacts: no exact production revision
  receipt, edge acknowledgement, public route response, or production deployment
  terminal result is present in the allowlisted fields.

## Inaccessible or intentionally withheld coverage

- Raw `$.data` streams, database table names, rows, SQL/constraint definitions,
  source/target shape values, URLs, environment/config values, and command lines were not
  copied into this index.
- The reopen plan has no terminal status field; it is plan/digest/generation evidence only.
- Nearby status/output pairs exist for schema diagnostics, shape extraction, reopen,
  smoke, and controller updates, but were kept metadata-only unless they were one of the
  six requested artifacts.
- This collection did not inspect or alter remote runtime state, start/stop services,
  build images, deploy, read credentials, use raw SSH/Docker, or mutate data.

## Nearby relevant filenames (metadata-only inventory)

The filename filter found 84 related files under `tmp` (schema/reopen/smoke/Lenzora/
deployment/reliability terms). The late-stage files most directly adjacent to the named
artifacts are listed here with UTC mtime and size; no content was read for these entries:

| mtime | bytes | filename |
|---|---:|---|
| 2026-09-08T15:21:23Z | 122807 | `sandbox-reliability-feedback.json` |
| 2026-09-08T15:21:25Z | 430568 | `sandbox-reliability-jobs.json` |
| 2026-09-08T15:21:34Z | 767 | `reopen-final-full-output.json` |
| 2026-09-08T15:25:08Z | 7859 | `reopen-final-full-status.json` |
| 2026-09-08T15:25:10Z | 7659 | `smoke-baseline-status.json` |
| 2026-09-08T15:25:25Z | 4490 | `smoke-baseline-stdout.json` |
| 2026-09-08T15:29:10Z | 12746 | `reopen-canary-live-status.json` |
| 2026-09-08T15:29:14Z | 736 | `reopen-canary-live-output.json` |
| 2026-09-08T15:30:29Z | 569 | `lenzora-production-image-status-current.json` |
| 2026-09-08T15:30:35Z | 7049 | `lenzora-production-status-current.json` |
| 2026-09-08T15:32:07Z | 390920 | `lenzora-remote-job-history.json` |
| 2026-09-08T15:32:18Z | 7269 | `lenzora-production-diagnose-current.json` |
| 2026-09-08T15:33:45Z | 0 | `lenzora-production-apply-log-current.json` |
| 2026-09-08T15:35:22Z | 692 | `smoke-lifecycle-output.json` |
| 2026-09-08T15:35:59Z | 7630 | `smoke-lifecycle-status.json` |
| 2026-09-08T15:45:48Z | 7647 | `smoke-lifecycle-fixed-status.json` |
| 2026-09-08T15:45:50Z | 3810 | `smoke-lifecycle-fixed-output.json` |
| 2026-09-08T15:53:02Z | 7653 | `smoke-route-diagnostic-status.json` |
| 2026-09-08T15:58:13Z | 7552 | `smoke-wake-fix-status.json` |
| 2026-09-08T15:58:15Z | 3704 | `smoke-wake-fix-output.json` |
| 2026-09-08T15:58:36Z | 586 | `remote-service-before-reopen-update.json` |
| 2026-09-08T16:01:11Z | 7872 | `reopen-controller-update-status.json` |
| 2026-09-08T16:01:13Z | 583 | `remote-service-after-reopen-update.json` |
| 2026-09-08T16:05:14Z | 9245 | `lenzora-dev-inspect-retained-restore-status.json` |
| 2026-09-08T16:05:15Z | 2319 | `lenzora-dev-inspect-retained-restore-output.json` |
| 2026-09-08T16:07:45Z | 9620 | `lenzora-dev-reopen-retained-status.json` |
| 2026-09-08T16:07:46Z | 1161 | `lenzora-dev-reopen-retained-output.json` |
| 2026-09-08T16:11:07Z | 9243 | `lenzora-dev-reopened-inspect-status.json` |
| 2026-09-08T16:17:29Z | 7865 | `schema-diagnostic-controller-update-status.json` |
| 2026-09-08T16:17:31Z | 2195 | `schema-diagnostic-controller-update-output.json` |
| 2026-09-08T16:17:50Z | 583 | `schema-diagnostic-controller-current.json` |
| 2026-09-08T16:20:22Z | 9236 | `lenzora-dev-schema-shapes-status.json` |
| 2026-09-08T16:20:23Z | 25891 | `lenzora-dev-schema-shapes-output.json` |
| 2026-09-08T16:24:00Z | 7853 | `schema-labels-controller-update-status.json` |
| 2026-09-08T16:11:08Z | 23231 | `lenzora-dev-reopened-inspect-output.json` |
| 2026-09-08T16:27:05Z | 59112 | `lenzora-dev-schema-labels-output.json` |
