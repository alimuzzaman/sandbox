# Host ingress evidence index

Live evidence exists for the default provider and for every read-only surface, on macOS.
No incumbent adapter is adoptable on any platform yet, so every adoption-lifecycle artifact
remains open.

| Evidence | State | Covers | Still required |
|---|---|---|---|
| `unit-gate.md` | complete | focused contract and security tests | — |
| `default-provider.md` | live (macOS) | T075: default Docker/Caddy ingress serves clean URLs with zero adoptable adapters | Linux; provider round trip |
| `listeners.md` | live (macOS) | T026: free/exact/wildcard/Sandbox-owned/foreign classification, non-mutation | Linux `/proc`+`ss` observer; IPv6 dual-stack |
| `support-and-consent.md` | live (macOS) | T063: tiers, pin precedence, non-interactive consent, credential-pending, detect-only | accepted consent + remembered decline (needs an adoptable adapter) |
| `compatibility.md` | live (macOS) | T069: Sandbox Caddy + per-port parity, corrected conflict diagnosis | Linux parity |
| `quickstart-run.md` | partial (macOS) | T068: read-only baseline, clean-URL serve, repeat-safety | the live incumbent lifecycle section |
| `cleanup.md` | partial (macOS) | T052: repeated cleanup, foreign preservation | owned-route, drift, unavailable-incumbent cleanup |
| `system-caddy.md` | pending live | T044: add/request/update/request/remove plus rollback | a Linux host running system Caddy |

## What each open item needs

- **T044** — a Linux host with system Caddy owning `:80`/`:443`, an enabled
  `/etc/caddy/conf.d/*.caddy` import, and the installed ingress helper. This is the first
  adapter that can be promoted to `adoptable`; nothing else in the adoption path can be
  proven until one adapter is.
- **T052 (remainder)** and the consent half of **T063** — depend on T044, because they need
  a route Sandbox actually owns.
- **T068 (remainder)** — the quickstart's live incumbent lifecycle section, which depends
  on T044.
- **T075 (remainder)** — a Linux run of `default-provider.md`, plus a
  `./sb domains use <adapter>` round trip, which again depends on T044.

## Rules for adding evidence here

Live proof must use `./sb`, show before/after host state, and state plainly what it does
NOT cover. A file that omits its gaps is worse than a missing file: it reads as coverage.
