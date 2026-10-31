# Sandbox code and backlog review

Date: 2026-10-27. Baseline: `latest` at
`a69e967e4887a074bcb457ed7b32f9beea66953e`, including the 18 existing pending
paths. This document consolidates source review and backlog inventory. No product
code was changed and no tests or live operations ran during this review.

**Verdict: fix the confirmed safety/correctness issues in bounded packages.**
The review found 14 distinct actionable issues: one Blocking and 13 Should-fix.
All predate the pending patch. No new defect was established in the pending edits
that reviewers examined. This does not clear unread code or establish release
readiness.

The requested full scope was inventoried, but semantic code coverage is partial.
The trust/security pass was interrupted. After the user raised quota consumption,
the parent stopped the overlapping remote pass and consolidated existing reports
without starting another agent. Remaining review work is explicit below.

## Findings

The source reports contain the full control-flow trace, disproof attempts,
confidence, falsifiers and proposed acceptance cases. Runtime reproduction is
unverified for every finding below. Links point to the reviewed working copy;
recheck them against a fresh revision before implementing.

| ID | Severity | Source and concrete failure | Next package |
|---|---|---|---|
| R01 | Blocking | [supervisor.py](../../../sandbox/jobs/supervisor.py#L219): an output/storage or other post-spawn failure marks the job terminal and releases its leases without stopping/reaping a still-running child. Another operation or cleanup can overlap its workspace. | W02 |
| R02 | Should-fix | [supervisor.py](../../../sandbox/jobs/supervisor.py#L139): closing stdout/stderr ends the selector loop and reaches an unbounded `wait()`. A live child can escape the submitted deadline; descendants holding pipes after leader exit are another uncovered case. | W03 |
| R03 | Should-fix | [owned-storage lifecycle service](../../../sandbox/owned_storage_lifecycle/service.py#L104): any completed acceptance can prove another remote. Saving an observed changed revision also allows the next read to clear drift without fresh acceptance. False support/adoptable reporting is established; a mutation-authority bypass was not established. | W05 |
| R04 | Should-fix | [job_service.py](../../../sandbox/application/job_service.py#L1316): retention repeatedly selects the newest bounded page. Preserved metadata for cleaned/ineligible jobs can permanently hide older retained artifacts, including under storage pressure. | W04 |
| R05 | Should-fix | [scheduler.py](../../../sandbox/activation/scheduler.py#L100): catalog refresh can remove a route while the coordinator still returns its due claim. The missing dictionary entry raises and kills the scheduler thread; catalog failure can produce the same mismatch. | W09 |
| R06 | Should-fix | [console.ts](../../../src/web/src/ui/console.ts#L44): rejected job polling leaves pause/busy flags and timers alive. Slow interval requests can also overlap and append the same output twice. Preserve unknown job identity rather than replaying the action. | W10 |
| R07 | Should-fix | [visit.py](../../../tools/visit/visit.py#L106): navigation records the first redirect status and can retain it instead of the final document response. A rendered 200 or 404 document can be reported as 302. | W11 |
| R08 | Should-fix | [hosting.py](../../../sandbox/commands/hosting.py#L3379): the second retirement shared-state lock can raise `TimeoutError` outside the JSON error handling, after a delivery action may already have happened. A fix must preserve truthful partial-effect state. | W06 |
| R09 | Should-fix | [secrets.py](../../../sandbox/commands/secrets.py#L740): local ownership configuration `ValueError` reaches the generic provider-failure catch, producing the wrong failure domain and exit category before any provider session. | W07 |
| R10 | Should-fix | [wp.py](../../../sandbox/commands/wp.py#L221): supported leading WP-CLI global options prevent command recognition for host-file staging. An outside-mount `eval-file` path then reaches the container unstaged. This is a staging correctness defect, not a demonstrated authorization bypass. | W08 |
| R11 | Should-fix | [destroy.py](../../../sandbox/hosting/destroy.py#L232): probe counts project-labelled explicit image tags, while teardown/final checks filter generated tag names. Destroy can report removal and clear state although a later probe still finds the image. Shared aliases make broad deletion unsafe. | W12 |
| R12 | Should-fix | [CLAUDE.md](../../../CLAUDE.md#L21): provider guides contradict each other on `main` writes, execution order and repository tooling edits. Current AGENTS rules resolve this run but do not repair the other entrypoint. | W01 |
| R13 | Should-fix | [TODO.md](../../../TODO.md#L549) and [spec index](../../../specs/README.md#L70): current queue claims conflict with source, task ledgers and feedback status. Examples include 046 planning/apply state, opt-in storage admission, 045 tasks, 033 evidence, config-subdirectory implementation, obsolete owner branches, 063 counts and omitted 066–069 entries. | W01 |
| R14 | Should-fix | [release-readiness.md](../../release-readiness.md#L49): a new wrapper is told to consume `app._run_sandbox_json`, while the active module rule forbids new `app.py` helper consumers. Existing compatibility callers do not authorize new ones. | W01 |

R13 merges `SD-002` and `FT05`; they are one queue-consistency package. R03 retains
both evidence-binding and persistent-drift scenarios under their common owner.
The interrupted second remote pass is corroborating partial work, not another
set of findings. Source-report IDs map as follows:

| Consolidated IDs | Source report IDs |
|---|---|
| R01–R04 | REMOTE-001–004 |
| R05–R07 | LC-01–03 |
| R08–R11 | FT01–04 |
| R12 | SD-001 |
| R13 | SD-002 and FT05 |
| R14 | SD-003 |

## Backlog and specification accounting

The supported feedback CLI walk inventoried 1,139 unique records. All 337 active
summaries were read: 224 unreviewed, 100 blocked, 10 in progress and 3 open.
Twelve selected detail projections were inspected. The other 326 active records
did not receive individual detail/source validation. Metadata for 802 non-active
records was indexed; their closure evidence was not re-audited. Clustering is
routing, not proof of a common root cause or a license to close records.

All 91 TODO bullet starts were indexed: 62 checkbox rows, four plain actionable
top-level bullets, 13 nested candidates, and 12 history/blocker/policy bullets.
There are 51 unchecked and 11 checked boxes. A checked box is a recorded claim,
not current runtime proof. The combined work index has 1,230 unique rows,
comprising all feedback IDs and TODO bullets. Records and TODO were not edited.

All 67 feature directories were indexed. Sixty have spec/plan/tasks artifacts,
seven are PRD-only, and 36 have a PRD. Literal task rows total 2,613 checked and
177 unchecked. These are task-ledger counts, not a behavior completion score.

Important distinctions for later work:

- **Real implementation gaps:** 060 lease/fencing/conversion integration,
  062 evidence reconciliation/fence release, and 064 edge transaction journal.
  Resolve the exact 051/054 prerequisites from source before assigning code.
- **Existing source with remaining proof:** 046, 061, 063, 066, 068 and 069,
  among others. Each open row needs its own evidence/authority classification.
- **Stale current queue claims:** feature 046 is beyond initial specification;
  storage-floor admission already exists as opt-in; 063 is 30/31 in its ledger;
  config-subdirectory behavior exists; some named local owner branches are absent.
  Local absence does not prove that external ownership has ended.
- **Historical or superseded work:** 018 is superseded by 023. Do not build a
  second recovery mechanism from its remaining unchecked rows. Use full feature
  directory identities for the two 042 directories; do not casually renumber them.
- **Partial live evidence:** the dated 2026-10-27 secret wire evidence records
  HTTPS client operations, human-approved migration reconciliation, scoped use
  and native credential placement. Blanket claims that no provider/native proof
  exists are stale. Cleanup, fresh-create/policy/race/worker/platform cases and
  human security review still have distinct outstanding obligations.
- **Deferred discovery:** the remaining PRDs and optional RexEnv integration
  stay outside reliability fixes. A possible first RexEnv slice is bounded,
  explicitly selected version/status/site inventory. No installation, MCP
  enablement or full runtime adoption was authorized or attempted.

## Coverage and unresolved limits

The repository inventory contains 2,573 paths. Coverage classes below belong to
different scopes and overlap; do not add them into a whole-repository percentage.

- Local code: 25 whole files read, 27 sampled, 221 unread in its 273-path scope.
- Completed remote pass: 24 whole files read, 40 sampled, 185 unread in its
  249-path review index.
- Feedback/TODO pass: seven whole files read, 40 source/document samples, 17
  symbol-only paths and 30 task-row-only paths. It is not a second full code audit.
- Specs/docs: all feature metadata and unchecked-task indexes scanned; selected
  contracts and bodies traced. Full prose across all ancillary artifacts was
  not exhaustively audited.
- Trust/security: the pass was interrupted. In particular, the PHP abilities
  raw-prefix containment lead remains unconfirmed and must not be advertised
  as an exploit or counted among R01–R14.

Priority unread areas include secrets and installation trust, hosted image and
recovery state machines, workspace authority, restore/capture adapters, native
adapters, remote pins/installers, server-config effects, remaining CLI/MCP
composition and desktop/Hermes paths. The execution plan includes bounded
continuation cards. No further agents are planned under the quota constraint.

The earlier 401-case passing evidence belongs to the preceding implementation
task and was supplied to reviewers. Six subsequent regression cases were added
but their runner failed before execution because of quota. No review performed
a test rerun, live Docker/controller observation, installed revision check,
credential access, provider request, deployment or cleanup. No source-only
verdict replaces those gates.

## Provenance and artifacts

Initial investigation used Astra medium. The completed remote, feedback/TODO and
specs/docs continuations used personal GPT-6.1-Sol medium with explicit standard
service tier. The local report remains Astra-authored. The parent consolidated
the existing reports directly; there was no new model review after the quota
objection. Titles containing “Astra” and the scratch directory name are
historical labels, not claims about resumed runtime settings.

All detailed evidence is local under
`/Users/alim/Sites/git/.scratch/sandbox/full-review-astra-2026-10-27/`:

- `local-code-review.txt` and `local-code-coverage.json`.
- `remote-code-review-sol-resumed-r1.txt`, the preserved completed report.
- `specs-docs-review.txt` and `spec-status-index.tsv`.
- `feedback-todo-review.txt`, `work-item-index.tsv` and coverage/detail indexes.
- `decisions.tsv` and pause/model-switch/overlap handoffs.

The original detailed reports retain falsifiers and test-gap descriptions.
The source findings are inputs to [the execution plan](execution-plan.md), not
permission to mutate providers, release code or treat historical incidents as
current host state.
