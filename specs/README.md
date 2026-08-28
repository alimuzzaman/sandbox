# Sandbox specs index

Spec-driven work for the Sandbox tooling. Material or ambiguous features may begin
with the first-class pre-spec artifact `prd.md`, owned by `speckit-refine`. Formal
Spec Kit progression is `prd.md` → `spec.md` → `plan.md` → `tasks.md`; later stages
may also add `research.md`, `data-model.md`, contracts, and quickstarts.

| # | Feature | Status | Origin |
|---|---------|--------|--------|
| 001 | Per-project-first instance model & modular `sb` | Complete | Internal rewrite |
| 002 | Snapshot & restore from the WP dashboard | Complete | Internal |
| 003 | In-instance WordPress Abilities + MCP Adapter layer | In progress | Novamira parity #1 |
| 004 | Async / background WP-CLI jobs | In progress | Novamira parity #2 |
| 006 | In-product skill authoring (auto-matched playbooks) | Complete | Novamira parity #4 |
| 007 | Headless debugging tools: Query Monitor + dump/dd + Xdebug | Complete | Debugging ask |
| 008 | DB-only snapshots & reset-to-fresh-install (extends 002) | In progress | Snapshot/reset ask |
| 033 | Agent-aware incremental remote sync | PRD | Remote development ask |

`Complete` means the task ledger has no open item and records the required
implementation and acceptance evidence. `In progress` means implementation exists
but at least one task or evidence gate remains open. Remote, live, and security-proof
gates stay open until their required evidence is recorded.

## Background: the Novamira comparison (2026-06-22)

[Novamira](https://novamira.ai/) is a WordPress plugin (Dynamic.ooo / Ovation
S.r.l., AGPL-3.0) that turns an *existing* WP install into an MCP server for AI
agents via the official **WordPress Abilities API + `wordpress/mcp-adapter`**.
Its core is one ability — `novamira/execute-php` (`eval()` with output-buffer +
error-handler capture) — surrounded by file CRUD, WP-CLI, and a skills system.

Novamira and the Sandbox are complementary, not competing: **Novamira brings the
agent to your WordPress; the Sandbox brings WordPress to your agent** (it
provisions, snapshots, and tears down per-project instances). The remaining
Sandbox specs cover MCP-client portability, skill authoring, and debugging on top
of its snapshot, multi-instance, and shipping-pipeline strengths.
