# Sandbox specs index

Spec-driven work for the Sandbox tooling. Material or ambiguous features may begin
with the first-class pre-spec artifact `prd.md`, owned by `speckit-refine`. Formal
Spec Kit progression is `prd.md` → `spec.md` → `plan.md` → `tasks.md`; later stages
may also add `research.md`, `data-model.md`, contracts, and quickstarts.

| # | Feature | Ledger status | Outside the ledger | Origin |
|---|---------|---------------|--------------------|--------|
| 001 | Per-Project-First Instance Model & Modular `sb` | Ledger closed | — | Internal rewrite |
| 002 | Snapshot & Restore from the WordPress Dashboard | Ledger closed | — | Internal |
| 003 | In-Instance WordPress Abilities + MCP Adapter Layer | Ledger closed | TODO.md: T012/T014/T022 external acceptance | Novamira parity #1 |
| 004 | Async / Background WP-CLI Jobs | Open (1 of 21) | — | Novamira parity #2 |
| 006 | In-Product Skill Authoring (Auto-Matched Playbooks) | Ledger closed | — | Novamira parity #4 |
| 007 | Headless Debugging Tools — Query Monitor, dump/dd, Xdebug | Ledger closed | — | Debugging ask |
| 008 | DB-Only Snapshots & Reset-to-Fresh-Install | Open (2 of 23) | — | Snapshot/reset ask |
| 009 | Single Swappable Per-User Base for All Sandbox Machine-State | Open (2 of 45) | — | — |
| 010 | Unified Slug-Keyed Plugin Config Map | Ledger closed | — | — |
| 013 | First-class WordPress Plugin Check support | Open (1 of 41) | TODO.md: T029 rerun | — |
| 014 | Remote VPS hosting for sandbox instances | Ledger closed | — | — |
| 015 | Managed Hosting with Cloudflare DNS and TLS | Open (1 of 33) | — | — |
| 016 | Remote Hermes Agent Integration | Ledger closed | — | — |
| 017 | Hermes State Sync | Ledger closed | — | — |
| 018 | Google Drive Full Backup | Open (4 of 12) | Superseded by 023 T060/T061 (TODO.md) | — |
| 019 | Hermes Public Dashboard Access | Ledger closed | TODO.md: T028 external acceptance | — |
| 020 | Reproducible Hermes Worker Routing | Ledger closed | — | — |
| 021 | Generic Project Instances | Ledger closed | — | — |
| 022 | Sandbox Modular Boundaries | Open (3 of 111) | — | — |
| 023 | Scoped Recovery Profiles | Open (4 of 76) | — | — |
| 024 | Default Reader.md Bootstrap | Ledger closed | — | — |
| 025 | Reliable Hermes Scheduled Work | Ledger closed | — | — |
| 026 | Lenzora TODO Worker | Open (1 of 7) | — | — |
| 027 | Hermes Authorization Controls | Open (1 of 27) | — | — |
| 028 | Test Execution Modes | Ledger closed | — | — |
| 029 | Generic Remote Deploy | Ledger closed | — | — |
| 030 | CLI-first Sandbox operation | Ledger closed | — | — |
| 031 | Remote and Hermes Operations Hardening | Ledger closed | — | — |
| 032 | Remote Job Runtime | Open (4 of 172) | — | — |
| 033 | Agent-Aware Remote Development Sync | Open (3 of 71) | — | Remote development ask |
| 034 | Google Drive Backups for Permanent Instances | PRD not ready | — | — |
| 035 | Resource Monitoring and Safe Cleanup | Open (1 of 57) | — | — |
| 036 | Deep Disk Attribution | Open (2 of 45) | TODO.md: T045 live evidence | — |
| 037 | Host Ingress Adoption | Open (3 of 80) | — | — |
| 038 | TLD and DNS Adoption | Open (4 of 70) | — | — |
| 039 | Native Runtime Adoption | Open (3 of 88) | — | — |
| 040 | xCloud API Adoption | PRD not ready | — | — |
| 041 | Safe Secret Inspection | Ledger closed | — | — |
| 042 | Sandbox Config Subdirectory | PRD ready | — | — |
| 042 | One-Click Host Storage Reclamation | Ledger closed | — | — |
| 043 | Scheduled storage-pressure monitor and safe-tier reaper | Ledger closed | — | — |
| 044 | Shared node store and hardlinked git workspaces | Open (3 of 19) | — | — |
| 045 | Managed Credential Vault and Isolation Evidence | Open (4 of 46) | — | — |
| 046 | Remote Host Swap and Memory Monitor Commands | Open (4 of 100) | — | — |
| 047 | Host Resource Governance | PRD ready | — | — |
| 048 | Observation-Only Hosting Recovery | Ledger closed | — | — |
| 049 | OCI Trust and Verification | Ledger closed | — | — |
| 050 | Secure Private Image Staging | Ledger closed | — | — |
| 051 | Immutable Activation and Recovery | Open (14 of 168) | — | — |
| 052 | Owned Storage Authority | Ledger closed | — | — |
| 053 | Instance-Scoped Server Configuration Fragments | Ledger closed | — | — |
| 054 | Recoverable Delivery Outcomes | Open (19 of 87) | — | — |
| 055 | Release Retention and Restore | PRD not ready | — | — |
| 056 | Remote nginx front door | PRD not ready | Shipped ahead of spec (ac9070b) | — |
| 057 | Scheduled Safe Cleanup on a Remote | Open (1 of 26) | — | Feedback 8a3e8c35 |
| 058 | Server-First Recovery Capture and Later Drive Promotion | Open (1 of 56) | — | Feedback 9e54f17b |
| 059 | Supervised Long-Running Secret Session | Open (1 of 30) | — | Feedback 2cfab06f |
| 060 | Per-Target Hosting Operations | PRD ready for speckit | Roadmap 2026-10-08 #1 | Feedback adccd6b7, 83dd053a, f72c4279 |
| 061 | Remote Runtime Revision Coexistence | PRD ready for speckit | Roadmap 2026-10-08 #2 | Feedback e41bef3b, be5a6353, f475f422 |
| 062 | Hosted Delivery Evidence Reconciliation | PRD not ready | Roadmap 2026-10-08 #3; amends 054 identity scope | Feedback 48c3e007, bae5cd4e, e4333c7b, f3329d32 |
| 063 | Remote Development Execution Readiness | PRD ready for speckit | Roadmap 2026-10-08 #5 | Feedback cef740dd, cebec97a, 5598f2d0 |
| 064 | Transactional Edge and DNS Changes | PRD not ready | Roadmap 2026-10-08 #4 | Feedback 6bd6bd1d, 50735fc8, 83cca354, 34af9b95, 075c6caf |

Statuses are counted from each feature's `tasks.md` (reconciled 2026-10-08). `Ledger closed`
means no unchecked task; `Open (n of m)` counts unchecked tasks, which are mostly
live, remote, or operator gates that stay open until their evidence is recorded.
A closed ledger is not by itself an acceptance claim: "Outside the ledger" names
gates tracked in `TODO.md` instead. `PRD ready` / `PRD not ready` are pre-spec
features (`prd.md` only). Two directories share number 042. Features 060–064
are ranked and sequenced in
[`docs/roadmap/2026-10-08-next-features.md`](../docs/roadmap/2026-10-08-next-features.md);
each needs an independent readiness review before `speckit-specify`.

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
