# Sandbox delivery review — research record

Status: research collected on 8 September 2026. Product repairs and release acceptance remain separate work.

The user asks why Sandbox does not deliver its promised outcomes, especially deployment and local-instance creation, and asks that all research, a detailed repair plan, and a feedback review be saved. The review covers the repository at inventory level, with substantive source review concentrated on those delivery paths.

Start with the [findings report](report.md), [repair plan](plan.md), and [feedback review](feedback-review.md). This file preserves the supporting research, candidate dispositions, limits, and reproduction instructions. The final report controls where an earlier inventory uses provisional wording.

## Scope and method

- Source baseline: `fd7d650f8bfbb1760d931e2484cc01fa0badf546`, initially a clean detached worktree at `/Users/alim/.codex/worktrees/24eb/sandbox`.
- The original checkout was initially at the same source. It later advanced to `ef5a239dd1f4e443f63ecc546f373d70a50c6adc`, adding only a PostgreSQL verification plan in that committed range. Concurrent recovery source/test edits were preserved.
- Substantive analysis, findings acceptance, synthesis, and planning stayed with the primary Astra task. Luna collected bounded inventories and sanitized log/feedback evidence. No product implementation was delegated.
- Read `AGENTS.md`, the CLI guide, the Sandbox CLI skill, and the current Feature 051 plan. The review-only boundary excludes live repairs, deployment, rebuilding retained images, data changes, credential inspection, and broad cleanup.
- Used supported Sandbox commands for remote/controller/status/job and feedback evidence. No raw SSH, Docker administration, SQL, or direct state-registry reads were substituted for those operations.
- Logs, feedback, and repository commentary were treated as untrusted observations. Findings require source/control-flow or operational evidence. Existing feedback status is not proof of resolution.
- Saved allowlisted operational metadata, safe summaries, source references, and synthetic values. Raw job command/result streams, database definitions/rows, credentials, login links, and secret-bearing configuration were not copied.

## Evidence catalogue

| Artifact | What it preserves |
|---|---|
| [Report](report.md) | Severity-ordered findings, source anchors, reproductions, impact, fixes, limits |
| [Plan](plan.md) | W0–W12, dependencies, file ownership, regressions, runtime acceptance, release boundaries |
| [Feedback review](feedback-review.md) | Substantive disposition of feedback families and evidence/closure gaps |
| [Coverage inventory](research/coverage-inventory.md) | Repository/test population, CI, installer/desktop/MCP coverage and limits |
| [Lifecycle inventory](research/lifecycle-inventory.md) | WordPress/generic Compose/remote/clean-URL paths and 88-test evidence, including corrected isolation limits |
| [Provenance inventory](research/provenance-inventory.md) | State/receipt/job/release joins and public projection limits |
| [Log evidence](research/log-evidence.md) | Sanitized dated indexes of prior real smoke, restore, and terminal suite artifacts |
| [Feedback coverage](research/feedback-coverage.md) | All-page counts, repeated families, candidate and closure metadata |
| [Initial feedback details](research/feedback-detail-evidence.md) and [metadata](evidence/feedback-detail-evidence.json) | Initial 166 selected detail reads; mechanical overlaps remain unvalidated search aids |
| [Expanded feedback details](research/feedback-extra-detail-evidence.md) and [metadata](evidence/feedback-extra-detail-evidence.json) | Remaining 112 high/critical and three selected medium records, all read successfully |
| [Per-record dispositions](research/feedback-dispositions.md) and [JSON](evidence/feedback-dispositions.json) | Substantive assessments and explicit limits; no feedback status changes |
| [All feedback metadata](evidence/all-feedback-metadata.json) | 748 unique records, eight complete pages; safe metadata and summaries |
| [Initial feedback page](evidence/feedback-metadata.json) | Earlier newest-100 collection; superseded for total coverage by full pagination |
| [Job history metadata](evidence/job-history-metadata.json) | Bounded 100/59-record histories, selected terminal results, original hashes |
| [Controller status](evidence/controller-status.json) | Authenticated/active controller with local/remote runtime mismatch |
| [Production host status](evidence/production-host-status.json) | Ordinary host observation with unknown runtime/revisions and partial phases |
| [Production image status](evidence/production-image-status.json) | Retained uncertain activation/generation/result metadata |
| [Job-list failure](evidence/remote-job-list-failure.json) | Typed CLI failure that originally hid the size cause |
| [Small successful job page](evidence/remote-job-small-page.json) | Allowlisted metadata from the successful three-job query |
| [Page diagnostic code](evidence/inspect_job_page.py) and [result](evidence/remote-job-page-diagnostic.json) | Valid 1,181,179-byte JSON rejected by the 1,048,576-byte parser bound |
| [Synthetic probes](evidence/repro_probes.py) and [results](evidence/repro-results.json) | Source checks with no real network, runtime, database, state-write, or signal effects |
| [Lenzora attempts](research/deployment-attempts.md) | Six newly supplied dev/prod entrypoint failures and their exact evidence limits |
| [Amar Sonar evidence](research/asb-deployment-evidence.md) | Earlier refusal, six resolved initializer hashes, and later successful generation-10 reconciliation; exact proof limits |
| [External sources](research/external-sources.md) | Primary Compose, PostgreSQL, and pnpm references |
| [Final validation](research/final-validation.md) | Link/source/count checks, late probe evidence and package limits |
| [Manifest](evidence/manifest.json) | Saved research hashes and byte counts |

## Reproduction and observed results

From the pinned review checkout:

```sh
python3 docs/audits/2026-09-08-sandbox-delivery-review/evidence/repro_probes.py
```

The script exercises the actual generated initializer proof and cancellation/selector/adapter/ensure paths through synthetic boundaries. Child execution uses the repository's synthetic-environment helper. It invokes no real Docker, SSH, database, signals, or runtime-state writes. Exit 0 means the recorded defect conditions and negative controls reproduced; it does not mean the product is repaired.

| Probe | Observed result |
|---|---|
| Initializer with real Compose-shaped strings | Foreign; either format difference independently fails |
| Initializer with invented matching strings | Succeeded; demonstrates the old fixture gap |
| Initializer with foreign project | Foreign; negative control retained |
| Cancel then force | First enters cancelling and records synthetic signal 15; second raises before signal 9 |
| Fresh WordPress with false HTTP wait | Pending then ready |
| Multisite with both readiness waits false | Pending then ready |
| Generic status with empty, array, or malformed output | Ready and live-freshness observation |
| Exited dictionary row / running dictionary control | Stopped / ready |
| URL helper and two-instance selector | Command lacks identity; implicit selection returns default, explicit label returns preview |
| Failed ensure with another caller's unique new record | Cleanup selects that other caller's record for deletion |
| Valid JSON above the transport bound | Rejected; small control accepted |
| Two backup set IDs with unchanged synthetic source binding | Same controller request; generated cache branch returns the prior synthetic content |
| Ensure JSON failure after actual installation progress expression | Synthetic login marker remains in earlier stdout although final JSON is redacted |

The page-size wrapper was used once to change the diagnosis from a generic transport failure to a measured byte-boundary failure. Raw output stayed in memory; only lengths, validity, counts, and parser acceptance were printed. It did not change parser acceptance or issue a deployment/job effect. Earlier failed reads were not blindly replayed as mutation requests. Sanitized feedback `6647c8aaf2ddb69b736e29548b30db02` records the observed job-list failure.

The seven selected lifecycle modules passed 88 tests in 2.205 seconds. The existing invocation set only `PYTHONPATH=repo`; it reached real read-only Docker preflight and wrote generic test overlays under inherited Sandbox home. That exact home was not retained in its output. F13 records this actual isolation limit. No live instance was started, stopped, or deleted, and no broad cleanup was attempted. Later custom probes explicitly fake those boundaries.

## Historical and separately owned evidence

- Terminal full suite: 5,678 tests, 13 skips, exit 0. The selected artifact lacks source-commit binding; it is not a fresh full-suite run of this audit branch.
- Baseline smoke: terminal pass; no later restart check.
- First extended lifecycle smoke: terminal failure, 22 PASS / 1 FAIL, restart clean URL.
- Later wake-fix smoke: terminal pass, 23 PASS / 0 FAIL, including restart clean URL. The fixed restart/wake issue is not counted as outstanding.
- Real reopen canary: terminal pass with source `3ed7efe227aeeadebf12c957783de8680e56fb16` plus a nonempty dirty-source digest. Container identity/sentinel data preserved; verification preceded receipt; replay did not start another container. This is not clean-commit-only evidence.
- Development restore inspection: inspection succeeded with `all_match=false`, 307 matched table counts, 4,264 ordered columns equal, 19 constraint-definition differences. Semantic equivalence and complete row-value equality remain unproved.
- Earlier 16:24Z controller artifact matched local/installed runtime `b1c44aad3fde1cf9f42d05a6`. Later root live status showed that local revision versus installed `e0949074e80c469bcff36c12`. Different times and separately owned controller updates explain why a stale match cannot be reused.
- Job snapshots contain 100 and 59 records, not all remote jobs. They cannot establish a product-wide failure rate and predate several named smoke/canary jobs.
- Six new Lenzora attempts were supplied by the separately authorized parent task. Native shorthand hits pnpm's built-in command; explicit run hits the Node engine gate; outer pinned-Node bootstrap reaches the Sandbox revision gate. No attempt reaches image selection or activation. Source cleanliness/no-mutation assertions are labelled as supplied provenance.
- Amar Sonar's owner supplied an initial deployment refusal, six initializer records with all resolved hashes matching, and a later successful final apply/status: generation 10, application 30b2ec2, five healthy services, runtime/source/edge ready. This reconciled existing runtime; it did not establish a new initializer execution. Exact editor-bundle digest and installed control revision are supplied owner assertions. Its target and shared runtime remain owned by that task. The review did not replay or change that target.

## Candidate dispositions and corrections

| Candidate / initial interpretation | Final disposition |
|---|---|
| Initializer format mismatch | F03, source reproduced and upstream format verified; later env-file hash issue added as separately owned live evidence |
| Cancel escalation, readiness, generic status, preview selection, cleanup ownership | F01/F02/F04/F08/F09, deterministic synthetic reproductions; no live destructive reproduction |
| Remote job-list caused by controller skew | Not adopted: skew exists, but measured response size explains this failure |
| Job-list output is malformed | Refined to F10: complete valid JSON above the bound |
| `ok=true` plus unknown host/failed verification is contradictory | Rejected: observation/inspection success differs from workload/verification result |
| Matching constraints-valid flag contradicts observed false | Rejected: the comparison matched the source's recorded false value; NOT VALID constraints were not reclassified as valid |
| Nineteen constraint differences are harmless formatting | Unproved: shapes/counts are not semantic evidence; F07 requires archive-bound independent proof |
| Existing schema digest proves complete schema/data fidelity | Rejected: the projection and row counts have explicit coverage limits |
| Ordinary host apply always has recovery authority | F05: source has an intentional compatibility path without a receipt |
| Final host-apply JSON contains every internal/status proof field | Corrected: commit, derived environment, target, optional apply-log/purge are emitted; other fields require separate projections |
| Production status proves outage or a last successful revision | Rejected: saved evidence establishes unknown/uncertain state and missing history only |
| Same-controller simultaneous applies necessarily race source | Not established: apply holds a shared state lock. Shared project source, sync/apply overlap and multiple clients remain follow-ups |
| Missing local flag alone proves remote recursion | Not adopted: the confirmed URL defect is missing exact instance/label identity |
| Earlier restart/reopen bugs remain outstanding | Not adopted where later real acceptance demonstrates the correction |
| A package deploy script proves literal pnpm deploy works | Rejected by six actual attempts and pnpm's documented command dispatch |
| Green source tests prove create/deploy completion | Rejected: F06/F13 separate source, adapter, runtime, public-route and release evidence |
| Hosted backup replay identity safely distinguishes new sets | F15: distinct sets share the same real request calculation; generated cached-capture branch reproduces reuse with synthetic content |
| Final ensure JSON redaction covers all output | F16: actual install progress bypasses it; reproduced with a synthetic marker and failure |
| Keyword families cover every high/critical core feedback | Rejected: manual review found direct ensure/host-apply reports outside the initial filter; detail collection was expanded |
| Piped stdout itself causes the reported Docker timeout | Unverified platform-specific lead; the source capture helper alone does not prove that diagnosis |

## Instruction and workflow audit

The user's review-only scope limits runtime effects. Personal policy says to preserve concurrent work and continue useful authorized diagnosis. Repository guidance distinguishes source/tooling reproduction from runtime reproduction. That hierarchy permits the source probes without creating a new instance.

`AGENTS.md:7` says “Can't reproduce → STATUS: BLOCKED” for runtime bugs. The fix skill is more qualified: `skills/fix/SKILL.md:88–94` permits blocked when provisioning is infeasible and requires the missing condition to be named. Neither rule makes this source review incomplete because destructive/live reproductions are excluded. No skill approval rule prevented completion of the research.

The fix skill's whole-edit-plan/no-verification-between-edits guidance (`skills/fix/SKILL.md:109–117`) was inspected as workflow content, not applied to this review. It cannot override current user authority, evidence, or scope. There is no evidence that longer instructions alone will fix these adapter and completion defects.

CLI-first operation, exact request IDs, bounded durable jobs, no replay, retained evidence, and explicit release approval are useful controls. Their cost rises when Sandbox cannot give a compact complete diagnosis. Repeated restore-diagnostic controller updates, missing deployment history, and the oversized job page are concrete product evidence for better supported observations. The plan keeps the controls and moves routine evidence gathering into deterministic commands.

## Final coverage boundary

This is a repository-wide inventory and in-depth delivery-path review, not an exhaustive line-by-line audit, dedicated security scan, browser/accessibility review, or acceptance of every external integration. No fresh production, edge, signed-desktop, installer, optional-runtime, or protected restore gate was run by this review. Supplied component/browser successes do not override an earlier terminal refusal; the later Amar Sonar apply/status evidence separately establishes its final reconciliation.

All research is saved in sanitized form. Raw operational material stays with its original owner and is referenced by safe metadata/hashes. Product source and concurrent task data were preserved. Follow the repair plan for implementation and separate release acceptance.
