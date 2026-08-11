# Sandbox PRDs — index, order, decisions

Date: 2026-08-11. Standalone product briefs, each owning one delivery phase. A PRD here is **not a spec**: it is the pre-spec brief an implementing agent converts through Spec-Kit (`speckit-specify` → `clarify` → `plan` → `tasks`). Nothing under `specs/` is created or modified by writing one. `TODO.md` at the repo root is reconciled against this set; finished work is deleted from TODO.md, not archived — history lives in git and the spec ledgers.

Convention follows the lenzora repo (`todo/NN-slug/prd.md` + this index + a reconciled root `TODO.md`).

| # | PRD | One line |
| --- | --- | --- |
| 00 | [Outbound mail](00-outbound-mail/prd.md) | Instances send as their own bound domain, direct from the Sandbox host — Postfix + OpenDKIM, capture-by-default, Cloudflare-automated SPF/DKIM/DMARC |
| 01 | [Herd-equivalent polyglot stacks](01-herd-equivalent-polyglot-stacks/prd.md) | Guided Laravel/database and Node environments with honest parity evidence, secret-safe configuration, and related-project coordination |

## Implementation order

```
now ─────────────────────────────────────────────▶
[TODO §1 loose ends]  [TODO §2 platform integrity]   (continuous, independent)
Phase 0 (00 outbound mail)  ─ independent; blocked only on the manual rDNS change
Phase 1 (01 polyglot stacks) ─ independent discovery; blocked on five owner decisions
```

## Standing decisions these PRDs inherit

- **Inbound mail is Cloudflare Email Routing**, catch-all on all seven zones to `alimuzzamanalim@gmail.com` (2026-08-06). No PRD proposes self-hosted inbound; anything needing IMAP/JMAP would have to argue against this first.
- **No paid mail provider** (owner decision 2026-08-06). Direct delivery is viable because outbound :25 is open on the host and the IP is unlisted — both verified, not assumed. A provider relay stays the documented fallback: one `relayhost` line.
- **Exposed routes are never indexable** — preview and control routes deny `/robots.txt` by default, fresh WP installs set `blog_public=0` (shipped `187f2bd`).
- **Capture beats convenience.** Anything that can email real people from a staging site defaults to off.

## TODO ↔ PRD coverage

| TODO.md item | Covered by |
| --- | --- |
| Instances cannot send mail; Mailpit is a dead end | 00 (entire) |
| Permanent hosted sites cannot send password resets | 00 §4.3 |
| `_cloudflare.py` cannot write TXT records | 00 §4.4 |
| rDNS is the generic hoster name on both IPs | 00 §4.2 (manual step) |
| `asb.bd` DMARC strict alignment blocks subdomain senders | 00 §4.2, decision 4 |
| Remote named `scaleway-sandbox` is actually Contabo | TODO §2 (naming/ops honesty; no PRD) |
| `hermes.asb.bd` robots.txt returns the Access 302 | TODO §1 (accepted gap) |
| Laravel/Node `sb init --type` labels require an existing Compose file | 01 §1, §5.2 |
| Herd-equivalent version and extension evidence is not reported | 01 §5.1, §5.5 |
| Related backend/frontend instances have no owned coordination contract | 01 §5.3 |
| The secret broker cannot deliver a declared application environment | 01 §5.4 |
| Exact MySQL 8.0.27 on Apple Silicon requires amd64 emulation | 01 §2.3, §12 |
