# Subagent research and execution ledger

Date: 2026-08-24

This ledger preserves the research handoffs and model routing used for this
audit. It contains sanitized summaries and output paths, not raw transcript
bodies, prompts, tool arguments, credentials, or private data. A report may also
be present in the original checkout when an agent inherited that checkout as
its current directory; copies are intentionally retained in both locations so
research is not lost.

## Completed planning

| Agent | Model/effort | Scope | Result |
| --- | --- | --- | --- |
| `audit_plan_sol` | `gpt-5.6-sol` High | Overall corpus, privacy, schema, Luna lanes, metrics, DeepSWE comparison, adjudication gates | Plan incorporated into [overall-plan.md](overall-plan.md); no product mutation |

## Collection and research lanes

| Agent | Model/effort | Scope | Durable result |
| --- | --- | --- | --- |
| `claude_luna_collect` | `gpt-5.6-luna` Max | Local Claude JSONL in Sandbox/T3-worktree roots; pilot, schema, duplicates, sanitized Sandbox signatures | [claude-corpus-summary.md](claude-corpus-summary.md) |
| `t3_luna_access` | `gpt-5.6-luna` Max | T3 app/browser metadata and supported export/access availability; no opaque-store decoding | [t3-access-report.md](t3-access-report.md) |
| `deepswe_luna_review` | `gpt-5.6-luna` Max | DeepSWE v1.1 repository/data/tasks/trials/artifacts/provenance/audit issues | [deepswe-review.md](deepswe-review.md) |
| `benchmark_landscape_luna` | `gpt-5.6-luna` Max | SWE-bench family, Terminal-Bench/Harbor, BrowserGym/WebArena, OSWorld, tau-bench, ToolSandbox | [benchmark-landscape.md](benchmark-landscape.md) |
| `codex_luna_collect` | `gpt-5.6-luna` Max | Full exact-CWD Codex rollout corpus, tool calls, failures, retries, polling, subagent and unfiled-ID coverage | [codex-corpus-summary.md](codex-corpus-summary.md) |

## Validation and execution planning

| Agent | Model/effort | Scope | Status |
| --- | --- | --- | --- |
| `corpus_adjudicator_luna_xhigh` | `gpt-5.6-luna` XHigh | Cross-report count consistency, coverage, lexical-vs-executed evidence, common-pattern ranking | [luna-xhigh-adjudication.md](luna-xhigh-adjudication.md); file-only commit `b7b928c` pushed |
| `sol_medium_execution_plan` | `gpt-5.6-sol` Medium | Plan remaining bounded implementation/reporting slices, Luna subtasks, tests, oracles, rollback, and gates | Complete in [sol-medium-execution-plan.md](sol-medium-execution-plan.md) |

The Sol planning/adjudication work is complete. Per the user’s later routing
instruction, no new Sol agent will be spawned; root performs fixture acceptance
and integration checks for subsequent Luna Max slices.

| `luna_synthetic_fixtures` | `gpt-5.6-luna` Max | L1.1 synthetic JSONL fixtures and expected normalized/exclusion/accounting rows | `fixtures/`; root accepted in [execution-decisions.tsv](execution-decisions.tsv) |
| `luna_schema_validator` | `gpt-5.6-luna` Max | L1.2 audit-only schema/parser/redactor/validator against synthetic fixtures | [l1.2-tooling.md](l1.2-tooling.md), `tools/audit_agent_usage/`, and `tests/audit_agent_usage/test_parser.py`; 13 tests passed twice |
| `luna_coverage_reconciler` | `gpt-5.6-luna` Max | L1.3 synthetic coverage/accounting reconciler with separate file/record/session/event/command units | [l1.3-coverage.md](l1.3-coverage.md), `tools/audit_agent_usage/coverage.py`, and `tests/audit_agent_usage/test_coverage.py`; 19 tests passed twice |
| `sol_high_adjudication` (final) | `gpt-5.6-sol` High | Final evidence inventory, pattern/benchmark conclusions, gate verdicts, and next-step plan after L1 evidence | [final-sol-high-plan.md](final-sol-high-plan.md); SHA `8450f22d290ff92bf83fdb94ee29ff110fa1163d78023ab9c2bc573fabcdb03e` |

## Preserved correction and access notes

- The Claude collector refined its lexical Sandbox-token total from 1,009 to
  **929** after excluding prose/assignment text: 343 in the Sandbox root and
  586 in T3 roots. The report labels these as approximate candidate hits and
  records the 193 tokenization errors. False positives and false negatives are
  possible.
- Claude timestamps are not strictly monotonic; duplicate marker rows are
  retained as diagnostics, not silently collapsed.
- T3 stores were inspected only as safe metadata. No transcript/export or
  owner-shared URL/API was verified, and no opaque database was decoded.
- DeepSWE has at least one currently readable representative ATIF trajectory,
  but public artifact availability is not uniform; `has_*` flags are not proof
  of a downloadable transcript.
- The benchmark landscape is a design comparison, not evidence that Sandbox
  has run those benchmarks.

## File-preservation incident record

Some Luna agents wrote their requested report into the original checkout rather
than the audit worktree because their inherited working directory differed from
the requested path. The parent copied the reports into the canonical audit
worktree and initially deleted the duplicate originals. That deletion was not
needed. On the user’s request, the four generated reports were restored in the
original checkout as well. No research content was intentionally discarded.

Going forward, every subagent must:

1. print and verify `pwd` and the absolute output path before writing;
2. write only to the canonical audit worktree unless the user explicitly asks
   for a second copy;
3. report `git status --short` for both the canonical and inherited checkout;
4. never delete a report to “clean up” a duplicate without explicit approval;
5. leave raw transcript material outside durable artifacts.
