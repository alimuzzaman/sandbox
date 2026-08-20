# Sandbox PRDs — index, order, decisions

Date: 2026-08-12. Standalone product briefs, each owning one delivery phase. A PRD here is **not a spec**: material or ambiguous work first runs through `speckit-refine` and its independent Sol High readiness review, then continues through Spec-Kit (`speckit-specify` → `clarify` → `plan` → `tasks`). Nothing under `specs/` is created or modified by writing one. `TODO.md` at the repo root is reconciled against this set; finished work is deleted from TODO.md, not archived — history lives in git and the spec ledgers.

Convention follows the lenzora repo (`todo/NN-slug/prd.md` + this index + a reconciled root `TODO.md`).

| # | PRD | One line |
| --- | --- | --- |
| 00 | [WordPress Plugin Release Guardian / Operations Agent](00-wordpress-plugin-release-guardian/prd.md) | One safe, revision-bound release verdict composed from deterministic gates, evidence-grounded AI triage, complete traces, a real-defect evaluation set, outside adoption, and published results including failure |
| 01 | [Outbound mail](01-outbound-mail/prd.md) | Instances send as their own bound domain, direct from the Sandbox host — Postfix + OpenDKIM, capture-by-default, Cloudflare-automated SPF/DKIM/DMARC |
| 02 | [Herd-equivalent polyglot stacks](02-herd-equivalent-polyglot-stacks/prd.md) | Guided Laravel/database and Node environments with honest parity evidence, secret-safe configuration, and related-project coordination |

## Implementation order

```
now ─────────────────────────────────────────────────────────────────────▶
[TODO §0 remote capacity blocker] ─ must precede new remote harness work
[TODO §1 loose ends]  [TODO §2 platform integrity]       (continuous)
Phase 0 (00 Release Guardian) ─ lead 12-month product; P0 safety and one gate first
Phase 1 (01 outbound mail)    ─ deferred unless a Guardian pilot needs real mail
Phase 2 (02 polyglot stacks)  ─ deferred; independent discovery remains valid
```

Phase 0 priority is internal as well: authority and fail-closed deterministic proof
come before AI triage; reliable remote harness admission precedes matrix claims;
tracing starts with the first runnable gate; evaluation and
pilot discovery begin before feature expansion; measured adoption and publication
complete the product rather than serving as optional marketing work.

## Standing decisions these PRDs inherit

- **Inbound mail is Cloudflare Email Routing**, catch-all on all seven zones to `alimuzzamanalim@gmail.com` (2026-08-06). No PRD proposes self-hosted inbound; anything needing IMAP/JMAP would have to argue against this first.
- **No paid mail provider** (owner decision 2026-08-06). Direct delivery is viable because outbound :25 is open on the host and the IP is unlisted — both verified, not assumed. A provider relay stays the documented fallback: one `relayhost` line.
- **Exposed routes are never indexable** — preview and control routes deny `/robots.txt` by default, fresh WP installs set `blog_public=0` (shipped `187f2bd`).
- **Capture beats convenience.** Anything that can email real people from a staging site defaults to off.
- **Deterministic gates beat AI confidence.** Guardian explanations never override a failed, missing, stale, or non-terminal required check.
- **Read-only is the Guardian default.** Mutation authority is scoped, revision-bound, expiring, replay-safe, and audited; publication/release remains outside the initial product.

## TODO ↔ PRD coverage

| TODO.md item | Covered by |
| --- | --- |
| Remote Docker subnet-pool exhaustion, admission evidence, and structured inventory failures | TODO §0, 00 §5 P0 |
| Safe, bounded WordPress abilities for a release agent | 00 §4.2, §5 P0 |
| Plugin Check + PHPUnit + compatibility + security as one release verdict | 00 §5 P0, §6 |
| Audit trails, traces, latency, resource use, and AI cost | 00 §4.3, §5 P0–P1 |
| Real-defect evaluation, outside adoption, results, and failure postmortem | 00 §5 P1–P2, §8 |
| Instances cannot send mail; Mailpit is a dead end | 01 (entire) |
| Permanent hosted sites cannot send password resets | 01 §4.3 |
| `_cloudflare.py` cannot write TXT records | 01 §4.4 |
| rDNS is the generic hoster name on both IPs | 01 §4.2 (manual step) |
| `asb.bd` DMARC strict alignment blocks subdomain senders | 01 §4.2, decision 4 |
| Remote named `scaleway-sandbox` is actually Contabo | TODO §2 (naming/ops honesty; no PRD) |
| `hermes.asb.bd` robots.txt returns the Access 302 | TODO §1 (accepted gap) |
| Laravel/Node `sb init --type` labels require an existing Compose file | 02 §1, §5.2 |
| Herd-equivalent version and extension evidence is not reported | 02 §5.1, §5.5 |
| Related backend/frontend instances have no owned coordination contract | 02 §5.3 |
| The secret broker cannot deliver a declared application environment | 02 §5.4 |
| Exact MySQL 8.0.27 on Apple Silicon requires amd64 emulation | 02 §2.3, §12 |
