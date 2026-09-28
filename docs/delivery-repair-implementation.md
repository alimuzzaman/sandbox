# Delivery repair implementation

Research is committed at `a1b6658fd51a75295563879a8df029b6ba28ff58` on
`codex/sandbox-delivery-review-20260908`. The pinned report remains historical;
this document tracks the implementation begun on 9 September 2026.

The first candidate follows the saved plan's Wave 1: W1–W4 and W12, with W8's
affected verification work after real command runs. Source changes and matching
documentation are completed together before writing or running tests. User-level
and project `AGENTS.md` now make that execution order critical.

## Owned changes

| Package | Change | Intended acceptance |
|---|---|---|
| W1 | Retain uncertain/reused instance data on ensure/preview failure; explicit project/instance URL updates and readback | Two labelled instances keep distinct URLs/data; failure never deletes an unrelated instance |
| W2 | Reuse the initializer correction from `0942d14` and `c7ed709`; bound resolved Compose configuration separately | Real Compose service/hash/image formats, env-file/dollar round trip, original no-replay checks |
| W3 | Stop on failed backend/multisite/final-route readiness; parse actual Compose status; remove login tokens from install progress | Usable fresh/reused instance; stopped/unknown/unhealthy status is truthful; full default output contains no token |
| W4 | Repeat/escalate owned cancellation; compact bounded CLI/MCP history with continuation | Real durable job cancellation and paged history; full records remain available through detail commands |
| W12 | Bind capture requests to backup set ID; retain admission, archive hash and capture times; serialize retries | Separate captures for distinct content generations; same-operation retry returns its original archive |

The source baseline is `fd7d650`; initializer source is integrated from the existing
correction without importing or running its tests during the coding phase. The
original checkout's active PostgreSQL edits and the Amar Sonar deployment checkout
are outside this candidate's write ownership. W5–W7's broader recovery/receipt
contracts, W9 feedback closure and W11's Lenzora entrypoint retain their separate
packages in the [saved plan](audits/2026-09-08-sandbox-delivery-review/plan.md).

## Behavior and protocol changes

WordPress keeps its pending name, ports and incarnation when installation cannot
reach its backend or final advertised route. Final route acceptance requires a
successful response or a redirect within the same origin. Login progress is always
secret-free; `ensure --json --reveal-login` cannot reveal inside a durable job.
Wake's backend-only liveness probe retains its existing separate semantics.
Ready-instance reuse also checks the final application route. Each ensure selects
one URL for WordPress repair, the readiness probe and the returned registry data;
repeated proxy observations cannot change that URL between these steps.

Compose status accepts JSON rows or an array. Empty output is stopped; malformed,
truncated or ambiguous output is an observation error. A running service is ready
only when its declared HTTP health path answers successfully. Container health and
application health remain visible in the result.

Job list page schema 2 is an additive envelope with `summary-v1` rows and a 512 KiB
page bound. Full command/submission/result payloads are detail-only. CLI and MCP
share the same projection; MCP retains opaque cursors. Old controller completeness
is unknown. Oversized old responses retain the 1 MiB transport refusal and a safe
size diagnostic. Use the installed/runtime revision check before remote acceptance;
do not migrate or downgrade the shared controller to make a read pass.

Hosted capture contract 2 requires an admitted backup operation ID. Its declared
controller capability is checked before capture. Each WordPress set/artifact has
one binding, one serialized capture, and a hash-checked receipt. Binding drift,
partial archive/receipt publication, or a legacy unreceipted cache fails closed.
This change does not update the concurrently owned PostgreSQL restore verifier.

## Verification sequence

1. Finish all source and documentation changes in this candidate.
2. Exercise actual supported commands on owned disposable fixtures, with finite
   deadlines and retained job IDs. Read existing remote state without updating
   the shared controller. Keep production and credential operations within their
   separate explicit authority.
3. Write focused regressions from those observed runs, fix affected fixture
   isolation, then run the required source and applicable runtime gates once.
4. Record exact source and runtime identities, command outcomes and limits below.
   Commit and push the candidate after the required checks. Deployment, release,
   merge and protected production acceptance remain separate decisions.

The first candidate is implemented and locally verified. The first focused run
executed 501 tests: 500 passed and one historical assertion still required unsafe
instance deletion. That assertion now requires retention; the corrected preview,
CLI/MCP pagination and selftest-environment selection passed 54 checks. After the
URL-selection correction, the affected readiness, PHP integration and environment
checks passed 48 tests.

The first full run executed 5,698 tests and found three fixture problems: an exact
old job-list envelope assertion, a stopped Compose fixture expecting ready, and an
unmocked Caddy health probe in a diagnostic test. The fixtures now assert the new
contracts and isolate that probe; all 39 affected checks passed. Product source
did not change in response to these three failures.

`selftest` now creates a temporary Sandbox home for its child suite and explicitly
sets the source import path. Affected unit fixtures also isolate artifact paths and
daemon preflight. The Linux smoke workflow now runs on pull requests affecting
lifecycle/runtime paths; it snapshots its disposable database before writing a
retention marker. No hosted CI result is claimed before that workflow runs.

## Findings from the first actual command runs

- Local job history returned two 100-row pages, 115,991 and 117,374 bytes, without
  overlap or full command/submission/result payloads.
- Real cancellation job `f7c7d874aa67996c057e61afbe1262fe` ignored TERM, accepted
  cancellation followed by force, and finished `cancelled` with exit `-9` and
  complete output.
- Fresh WordPress job `34b9b6c64aa6385f1e7b51c902722e4d` installed WordPress but
  stopped at the new backend readiness gate. Backend probing must not follow a
  canonical redirect into the separately checked clean-URL/DNS/TLS path.
- Creation job `29029332256e8435a63d9ed4b15d5858` exposed a real allocation bug:
  WordPress and Mailpit both received port 8251. The allocator now reserves each
  choice within a trio; ensure repairs duplicates only for its selected instance.
  Startup diagnostics now retain both ends of a bounded, redacted error so the
  final daemon cause is visible.
- The first fixture's Git-derived name was initially misread as parent-project
  selection. Its retained output proves the requested nested root was used and
  the job had failed before cancellation was considered. Feedback `7b15b46c` was
  corrected to invalid; it is not a new product finding.
- The real Compose initializer completed once with an env-file value containing
  a literal dollar. The actual hosted initializer observer ran twice, returned
  `succeeded` both times and left the counter at one. This proves the observer
  against Compose 5.1.2, not an entire hosted deployment.
- Real smoke job `4c2d53ee3e8c0b3a641cf0cafb704c70` passed 23 checks: captured
  startup, repeat ensure, retained data after restart, canonical REST URLs and
  owned fixture cleanup. The later ready-path change has separate reuse evidence.
- Ready-path job `51944e1241ace1da8abb4c65912aee41` exposed inconsistent URL
  selection: repair chose localhost while a later proxy observation chose HTTPS.
  The strict gate refused success. Feedback `d985c36d` records the actual browser
  and WordPress-option mismatch; one selected URL now spans the whole operation.
- Explicit-instance snapshot from the tooling checkout recreated the owned web
  container and the next ensure correctly refused `instance_mount_drift`. The
  supported project-scoped apply succeeded in job
  `eebeec0602789516bc261eeaf0d006d3`. Feedback `68cefe5c` retains this separate
  selector/predispatch issue for follow-up; its root cause is not yet established.

## Evidence

[Machine-readable command evidence](evidence/delivery-repair-20260909.json)
contains terminal job metadata, actual browser and option observations, page bounds,
feedback IDs and the exact changed-file hashes supplied to verification.

The final real reuse job `6ded418a4e0f3b5e8eb4adead7316ac6` ran two ensure calls.
Both returned the same HTTPS URL stored in WordPress `home` and `siteurl`; both
browser loads returned HTTP 200 without navigation or network errors. The original
instance incarnation stayed unchanged, and neither captured default output exposed
an autologin value. Owned WordPress and Compose fixtures are now stopped with data
retained; the smoke command removed its own disposable fixture.

The final real lifecycle smoke, job `635f4f0bb19716e2e6a08b6ec915cb4c`, passed all
23 checks after the URL-selection fix and fixture corrections. It covers fresh
creation, reuse, restart, retained data, canonical REST URLs and owned cleanup.
The final full suite, job `19f1f8cd348603a9af989b5f55dd4571`, succeeded with exit 0:
5,698 tests in 362.846 seconds, 21 skipped and no failures. It used the same base
commit `a1b6658fd51a75295563879a8df029b6ba28ff58` and digest
`sha256:c0ce43e70d833bb6163c3be317ab41e821ee89faaa91174ccfb25eae71e6756d`.
This digest covers all 32 changed source, test and workflow files; documentation
is excluded so recording results cannot alter the execution fingerprint.
Those hashes still match the candidate after both terminal successes. All 31
changed Python files parse, all 29 checked local Markdown links resolve, and the
changed-file secret-pattern scan and `git diff --check` passed. The research
package remains unchanged, and the original checkout's eight PostgreSQL edits
remain outside this branch.
A previous queued full-suite job was cancelled before any process started when the
ready-path gap was found. It is not counted as a test run.

## Release limits

The shared remote remains on runtime revision `bebe6ee03db17fb10c4c1b73`.
It was inspected through the supported status command and was not migrated.
New-controller pagination and production capture contract 2 have not been accepted
on that remote. The existing production capture adapter targets Amar Sonar and
needs a separately authorized capture run; local receipt/archive regressions are
not production backup freshness evidence. Remote two-instance URL/data acceptance,
hosted initializer deployment acceptance and the newly triggered Linux CI smoke
also remain release gates. These changes affect instance ownership, secret output
and backup identity and require human review before release.
