# Sandbox specs index

Spec-driven work for the Sandbox tooling. Each numbered directory is a feature
spec (spec-kit shape: `spec.md` + `plan.md`, sometimes `research.md` /
`data-model.md` / `tasks.md`).

| # | Feature | Status | Origin |
|---|---------|--------|--------|
| 001 | Per-project-first instance model & modular `sb` | In progress | Internal rewrite |
| 002 | Snapshot & restore from the WP dashboard | In progress | Internal |
| 003 | In-instance WordPress Abilities + MCP Adapter layer | Draft | Novamira parity #1 |
| 004 | Async / background WP-CLI jobs | Draft | Novamira parity #2 |
| 006 | In-product skill authoring (auto-matched playbooks) | Draft | Novamira parity #4 |
| 007 | Headless debugging tools: Query Monitor + dump/dd + Xdebug | Draft | Debugging ask |
| 008 | DB-only snapshots & reset-to-fresh-install (extends 002) | Draft | Snapshot/reset ask |

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
