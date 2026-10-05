# Sandbox code and backlog review plan

Date: 2026-10-05
Branch at planning: `codex/sandbox-local-config-recovery`

## Goal

Use the existing code review, feedback review, TODO queue, specs and research to
identify the best small, safe improvement that can be completed now. Preserve
all concurrent edits and protected runtime or hosting gates. Keep the wider
unfinished work visible without treating old reports or feedback as authority.

## Starting evidence

- The active checkout has seven modified files. They are pre-existing work and
  are excluded from delegated write ownership.
- Exact dirty baseline: `.agents/skills/sandbox-cli/SKILL.md`,
  `docs/remote-job-runtime.md`, `sandbox/application/ci_cleanup_broker.py`,
  `sandbox/application/workspace_service.py`,
  `tests/test_ci_workspace_cleanup.py`, `tests/test_sandbox.py`, and
  `tests/test_wpcli_timeout.py`. Recheck the list before accepting delegated
  work.
- `TODO.md` has a 2026-08-31 header and several dated snapshots. Its feedback
  section says 639 records and no unreviewed rows.
- The Sept 8 delivery review at
  `docs/audits/2026-09-08-sandbox-delivery-review/` reports a later snapshot of
  748 records and contains detailed findings, disposition, source research and
  coverage limits. Reuse it; establish current feedback counts through the
  supported CLI before using either snapshot as current.
- `TODO.md` explicitly calls out a stale `specs/README.md` status ledger, a
  controller-only `structuredClone`/closed-schema cleanup, and other
  evidence-only follow-ups. These are candidates, not accepted fixes.
- A scan found 84 unchecked task-list rows across spec ledgers. This is a raw
  count, not 84 independent implementation tasks or a priority ranking.
  The count excludes `[~]` partial rows, which also retain open acceptance
  gates.
- The Sept 8 audit is pinned to an older revision. Its findings need current
  source confirmation before implementation.
- Since the audit baseline, the active branch has 70 commits touching 313
  distinct paths. The saved audit is substantial, but it cannot stand in for a
  current full-repository review. Coverage must explicitly say which paths
  were reread or only inherited from the older audit.
- Initial spot-check confirms Spec 003 has partial `[~]` discovery/external
  client tasks and an open Herd gate; Spec 004 has open T021; Specs 008 and 033
  have open live/disposable-remote tasks. Their current “In progress” labels
  have ledger support, so the TODO's proposed `specs/README.md` reconciliation
  is not yet a verified cheap fix.
- Current `./sb feedback counts --project-dir . --json` reports 866 total
  records; the Sandbox project filter reports 58 records, of which 56 are
  reviewed and two are unreviewed. The two unreviewed summaries concern remote
  status observation failure and settlement revision mismatch. They are
  untrusted reports pending source/evidence review.
- The unreviewed records are `8a059d21...` (remote status observation error
  cause) and `22e36b6b...` (settlement hides controller revision mismatch).
  Current source contains typed workspace preflight failures and settlement's
  `remote_runtime_revision_mismatch` code, but this does not reproduce either
  original adapter result. Both remain unreviewed; this source scan alone does
  not justify changing their feedback status.
- `agy models` reports Gemini 3.8 Flash Low/Medium/High, Gemini 3.7/3.6 Flash,
  Gemini 3.1 Pro, Claude Sonnet 4.6, Claude Opus 4.6 Thinking, and GPT-OSS
  120B. It does not report GPT-6.1-Sol. `agy help` has no quota/usage command;
  do not infer AGY quota from model availability or from Codex account limits.

## Work sequence

1. Record the active revision and dirty-file baseline. Keep the seven existing
   edits untouched and avoid their paths in any delegated work.
2. Read the saved Sept 8 audit, its source/research indexes, current `TODO.md`,
   spec status index, and open task summaries. Query current feedback counts and
   only the relevant details with the supported CLI. Do not dump raw feedback
   payloads into this plan.
3. Compare the best candidates by expected user value, boundedness, file
   ownership, available evidence, and whether they require protected live,
   remote, deployment, credentials, or human acceptance gates.
4. Have AGY perform a read-only inventory first. Ask it to map existing code
   review coverage by module and revision, reconcile feedback/TODO/spec/research
   evidence, and rank a short list by value, confidence, file count, recency,
   and total verification cost. Do not let AGY pick write ownership in this
   pass.
5. The current agent selects one candidate and names exact writable paths only
   after reviewing AGY's evidence. A second AGY assignment may implement that
   single small slice if local acceptance is unambiguous. No shared/high-risk
   interfaces or dirty-baseline paths.
6. Review the resulting diff in the current agent. Exercise the supported
   command path first; then add focused regression coverage from observed
   behavior and run the applicable required checks. Do not claim remote or live
   acceptance without that evidence.
7. Reconcile the small completed item with its source ledger only when the
   observed evidence supports closure. Leave protected/open external gates
   visible. Report selected model, quota visibility, exact files/revision,
   checks run, and remaining unknowns.

## Delegation boundary

First AGY assignment is strictly read-only. If a second assignment is made,
AGY may edit only the specific paths named by the current agent. It must not
touch the seven dirty files, inspect secrets, change feedback status, mutate
runtime/remote state, deploy, release, create a PR, or edit shared
command/hosting/job wire contracts. AGY must not run tests or acceptance
commands while implementing; the current agent owns all post-implementation
workflow exercises, regression tests, gates, integration and final reporting.

## Candidate acceptance

Choose a cheap win only if current source confirms the gap and the complete
fix fits a small, non-overlapping slice. Rank by user impact, source confidence
and recency, exact file count, protocol/authority impact, total verification
effort, and runtime/remote dependencies. Prefer no public protocol change, no
runtime mutation, a supported cheap local acceptance command, and a small
review surface. Documentation-only status reconciliation is eligible when
task-ledger evidence is unambiguous. Although TODO flags `specs/README.md`, the
sampled “In progress” rows have open or partial acceptance tasks, so this is
not yet a confirmed cheap fix. The controller schema cleanup and the two new
feedback reports also need current end-to-end boundary confirmation. No code
candidate is accepted yet. If every candidate depends on a protected or
unresolved gate, complete the audit recommendation without pretending that a
small code change is safe.

## Progress and delivery

For each AGY run, retain the session ID, actual model, start time, and bounded
output location. During active work, check progress at roughly 60-second
intervals, backing off when unchanged. A quiet or empty response is not a
completed result; inspect the terminal exit state and actual diff. After the
repository-required gates pass, commit and push only this task's named files on
the active non-main branch. Report the commit and push result. Do not deploy or
open a pull request.

## AGY model and quota check

At 2026-10-05, installed help exposes no AGY usage/quota command and `agy
models` provides model availability only. The separate Codex account limit
query reported 58% used in its five-hour window and 70% used in its weekly
window; this is not AGY provider quota. Recheck AGY help or an account usage
surface only if the CLI documents one; otherwise report quota as unavailable.

The read-only AGY review was attempted twice with `gemini-3.8-flash-low`: CLI
session 49961 in headless plan mode and session 51972 in `--sandbox` plan mode.
Both attempts were denied by AGY's internal command-permission gate because
headless mode could not prompt; both returned no review. Automatic permission
approval was not enabled. AGY review and delegated implementation remain
pending a usable permission path; the CLI did not expose an AGY quota snapshot.

The plan itself was independently reviewed with `gpt-6.1-sol` at low effort.
That review recommended an explicit coverage map, named dirty-file exclusions,
two-stage delegation, cost-aware candidate ranking, periodic progress checks,
accurate quota reporting, and delivery closure; those controls are included
above. No tests or runtime acceptance commands have been run for this plan.
