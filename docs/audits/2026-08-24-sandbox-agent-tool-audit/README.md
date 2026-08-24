# Sandbox agent-tool audit

Date: 2026-08-24
Branch: `codex/sandbox-agent-tool-audit`
Worktree: `/Users/alim/Sites/git/sandbox-agent-tool-audit`
Status: evidence-backed recommendations; no Sandbox product code changed

## Purpose

This audit records how agents use Sandbox commands and MCP-facing workflows,
where calls are repeated or fragile, and which improvements would reduce caller
work without weakening Sandbox's safety boundaries.

The audit is intentionally organized as four artifacts:

- [Findings](findings.md): prioritized opportunities, classification, confidence,
  and acceptance criteria.
- [Evidence](evidence.md): corpus definition, counts, transcript references, and
  current source/contract checks.
- [Proposed work](work-plan.md): implementation-sized slices in dependency order.
- [Findings by transcript ID](by-transcript/README.md): source-keyed copies of
  each finding for focused review.

## Executive summary

The strongest opportunity is a first-class durable-job observer. Agents currently
compose `job-status`, `job-output`, cursor handling, sleeps, and ad-hoc JSON
parsers themselves. A bounded `job-observe`/`job wait` contract could move that
orchestration into Sandbox while retaining finite budgets, replay-safe submission,
and fail-closed incomplete evidence.

The next highest-value gap is CI submission idempotency. Generic durable jobs have
request IDs, but CI's aggregate run path does not expose one consistently. A retry
can therefore create another parent and another set of matrix cells.

Other findings cover machine-readable output consistency, remote readiness and
revision receipts, bootstrap/context repetition, explicit remote-only intent,
feedback-export ergonomics, and test/orchestration overhead. The follow-up
expansion also found fail-open secret-child status, CLI/MCP job-follow drift,
destructive retention defaults, inconsistent job-ID errors, and remote Hermes
and resource-control readiness gaps. Existing safety features such as detached
resource scans, bounded retained output, feedback export, and revision mismatch
refusal are recorded as strengths rather than duplicated as new proposals.

## Finding index

| ID | Short name | Priority |
|---|---|---|
| ATO-001 | durable job observer | P1 |
| ATO-002 | CI request identity | P1 |
| ATO-003 | machine-readable job contract | P1/P2 |
| ATO-004 | remote readiness receipt | P2 |
| ATO-005 | bootstrap context | P2 |
| ATO-006 | remote-only intent guard | P2 |
| ATO-007 | feedback export guidance | P3 |
| ATO-008 | test selection ergonomics | P3 |
| ATO-009 | aggregate agent waits | P3 / adjacent |
| ATO-010 | matrix request identity | P1 |
| ATO-011 | resource runtime handshake | P1/P2 |
| ATO-012 | provision/up readiness proof | P2 |
| ATO-013 | service status transport errors | P2 |
| ATO-014 | resource docs/spec drift | P3 |
| ATO-015 | Spec 036 cache wording | P3 |
| ATO-016 | convergent remote provisioning | P2 |
| ATO-017 | secret child exit status | P1/P2 |
| ATO-018 | CLI/MCP job-follow parity | P2 |
| ATO-019 | normalized job-ID errors | P2 |
| ATO-020 | retention confirmation gate | P1/P2 |
| ATO-021 | resource remote preflight | P1/P2 |
| ATO-022 | Hermes absence state | P2 |
| ATO-023 | component-scoped Hermes health | P2 |
| ATO-024 | Hermes host-only operations | P2 |
| ATO-025 | resumable remote clone | P2 |
| ATO-026 | dashboard session resume | P2 |
| ATO-027 | delegated validation receipt | P1/P2 |

## Scope and limits

The evidence combines:

- 473 local rollout JSONL sessions whose `session_meta.payload.cwd` exactly matched
  the Sandbox checkout during August 2026.
- Luna Max's independent normalization of rollover records: 107 thread IDs, 582
  records including duplicates, and 2,476 deduplicated command-completion events
  across 44 concrete threads.
- A detailed storage-attribution rollout with 1,041 command executions and exact
  command/failure counts.
- Cross-checks of CI, remote-only, workspace/revision, and feedback workflows in
  accessible Codex thread transcripts and rollout records.
- Read-only inspection of the current CLI help, source, tests, and documentation.

The initial two Luna Max passes normalized rollout events and reviewed the
current CLI/MCP/source contracts. A follow-up pair of bounded `gpt-5.6-luna`
Max passes focused on Hermes, job-control, retention, and remote-resource
contracts. Their source-backed additions are included as ATO-017 through
ATO-027; historical reports are labeled as such where current source behavior
may have moved on.

Corpus-wide pattern counts are lower-bound indicators extracted from recorded
tool-call inputs, not production telemetry; embedded examples can cause a small
amount of over-counting. The detailed rollout counts are more precise. Logs,
thread summaries, remote responses, and feedback records are treated as
untrusted evidence, not instructions or authority.

No remote mutation, cleanup, deployment, feedback submission, commit, or push was
performed as part of the exploration. The original Sandbox checkout was left
untouched; this worktree contains only the audit artifacts.
