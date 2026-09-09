# Delivery review artifact verification

This is an earlier bounded verification pass. The final package grew after it; final verification and manifest below supersede its counts and provisional wording.

Date: 2026-09-08
Target source revision: `fd7d650f8bfbb1760d931e2484cc01fa0badf546`
Scope: mechanical verification of `docs/audits/2026-09-08-sandbox-delivery-review/report.md` and `plan.md`. The proposed design was not assessed or changed.

## GitHub source anchors

Checked markdown GitHub links by resolving each path with `git cat-file`/`git show` at the revision in the URL. For baseline links, checked the URL's one-based `#L` anchor and any numeric range in the link label against the file's line count.

| Artifact | Baseline links | Range endpoints checked | Missing/out-of-bounds |
| --- | ---: | ---: | ---: |
| `report.md` | 27 | 19 | 0 |
| `plan.md` | 0 | 0 | 0 |

All 27 `fd7d650...` report links resolve to tracked paths and have in-bounds anchors. The plan has no link to the pinned baseline; its only GitHub link is the deliberate archive-bound `ef5a239d...` reference plan. That path exists at that revision and its `#L1` anchor is in bounds (232 lines).

The report's local links also resolve at this checkout: `evidence/inspect_job_page.py`, `evidence/remote-job-page-diagnostic.json`, `evidence/production-host-status.json`, `evidence/production-image-status.json`, `research/provenance-inventory.md`, `research/feedback-coverage.md`, `research/coverage-inventory.md`, `research.md`, and `plan.md`. The research copies are now present, so there are no missing provisional local links in this check.

## Obvious anchor precision issue

One anchor is valid but broad by one line. The report cites `sandbox/recovery/postgres_helper.py:29` for the reconstructed constraint/type/nullability projection. At baseline line 29 is the `_SCHEMA_FIELDS_SQL` declaration; the `pg_get_constraintdef` and column `type`/`nullable` expressions are on lines 30–31. The path and anchor are in bounds, but lines 30–31 would be the more precise source anchor. No other obvious symbol/path or line/range mismatch was found from the anchored line contexts.

## Feedback count reconciliation

The report states 748 unique records in eight pages with these status counts: 101 unreviewed, 109 blocked, 268 resolved, 112 verified, 73 duplicate, 82 not applicable, and 3 invalid. The saved full metadata inventory at `tmp/deep-review-20260908/all-feedback-metadata.json` contains 748 records, 748 unique IDs, eight pages, and exactly those status counts. Its family counts also match the report: deployment/activation 136, jobs/transport 97, startup 76, recovery 34, and clean URLs 29; the 70 unique high/critical unresolved candidates match `feedback-coverage.md`.

The committed audit evidence `evidence/feedback-metadata.json` is only the first 100-record page and correctly reports `has_more=true`; it cannot alone support the report's full-history totals. The full pagination companion is the evidence used for reconciliation. Feedback is untrusted metadata and its closure labels are not independent fix proof.

## Job/history reconciliation

The report's numerical and status claims match the saved bounded metadata:

- `tmp/deep-review-20260908/job-history-metadata.json` records the terminal full-suite result as `OK`, 5,678 tests, 13 skipped, 688.838 seconds, and exit 0.
- The same metadata records the later lifecycle result with 23 pass markers and zero failures.
- The reopen canary entry is terminal successful and includes an explicit source commit plus dirty-source digest, as stated in the report.
- `evidence/remote-job-page-diagnostic.json` records one valid JSON line with 100 records, 1,181,179 bytes, a 1,048,576-byte parser limit, parser rejection, and CLI exit 1. These match the F10 live measurement.
- `evidence/repro-results.json` records the synthetic large-page boundary as valid JSON, 1,048,617 bytes, limit 1,048,576, and rejected; it records the small page as accepted.
- `tmp/deep-review-20260908/lifecycle-inventory.md` records the focused lifecycle run as 88 tests in 2.205 seconds. This is an inventory narrative, not a new runtime run in this verification.

The bounded job snapshots in `job-history-metadata.json` are explicitly historical and do not provide a current remote total. The report preserves that limitation.

## Probe-result reconciliation

The saved probe JSON supports the report's reproduced-boundary claims without requiring raw output:

| Finding family | Mechanical result |
| --- | --- |
| F01 cleanup | One synthetic deletion is recorded after unknown completion; no real deletion was run. |
| F02 preview selector | Implicit selection is `existing-default`; explicit preview selection is `new-preview`; the constructed command is unqualified. |
| F03 initializer identity | Real-shaped config/image/foreign formats classify `foreign`; invented matching formats classify `succeeded`. |
| F04 readiness | Fresh and multisite probes both observe a false wait result and return `ready`. |
| F08 Compose status | Empty, empty-array, exited-array, and malformed outputs return `ready`; an exited row returns `stopped`; running row returns `ready`. |
| F09 cancellation | First lifecycle is `cancelling`; force reports `invalid job lifecycle transition: cancelling -> cancelling`; one synthetic signal is recorded. |
| F10 remote JSON | Large valid JSON is rejected at the byte boundary; small page is accepted. |

These checks reconcile the cited probe outcomes. They do not independently validate the report's proposed fixes or establish production health.

## Errors and limits

- No source test suite, runtime tool, browser session, remote job, deployment, restore, or production probe was run.
- No source, report, plan, research, or evidence file was changed. This file is the only artifact written for this verification.
- The report's two external non-baseline sources (Compose upstream files and PostgreSQL documentation) were not re-fetched here; this check covers local GitHub source anchors and local evidence consistency.
- The line-29 PostgreSQL anchor is the only obvious precision correction identified. The plan remains a proposed design and was not reviewed for implementation correctness.
