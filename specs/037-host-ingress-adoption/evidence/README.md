# Host ingress evidence index

Live evidence exists for the default provider and for every read-only surface, on macOS.
No incumbent adapter is adoptable on any platform yet, so every adoption-lifecycle artifact
remains open.

| Evidence | State | Covers | Still required |
|---|---|---|---|
| `unit-gate.md` | complete | focused contract and security tests | — |
| `default-provider.md` | live (macOS) | T075: default Docker/Caddy ingress serves clean URLs with zero adoptable adapters | Linux; provider round trip |
| `listeners.md` | live (macOS) | T026: free/exact/wildcard/Sandbox-owned/foreign classification, non-mutation | Linux `/proc`+`ss` observer; IPv6 dual-stack |
| `support-and-consent.md` | live (macOS + Ubuntu) | T063: tiers, pin precedence, full consent lifecycle, credential-pending, detect-only | NPM credential storage |
| `compatibility.md` | live (macOS) | T069: Sandbox Caddy + per-port parity, corrected conflict diagnosis | Linux parity |
| `quickstart-run.md` | live (macOS + Ubuntu) | T068: read-only baseline, clean-URL serve, repeat-safety; lifecycle in `system-caddy.md` | the live transaction-failure matrix |
| `cleanup.md` | live (Ubuntu + macOS) | T052: normal, repeated, drift, incumbent-unavailable cleanup with foreign routes healthy | incumbent replaced by another product |
| `system-caddy.md` | live (Ubuntu 24.04) | T044: add/request/update/remove through a real incumbent, incumbent routes preserved | HTTPS/wildcard; the live transaction-failure matrix |

## What each open item needs

- **T044** is captured on Ubuntu 24.04: adoption serves HTTP 200 through the incumbent,
  repeats safely, and cleans up completely with the incumbent's 16 pre-existing routes
  untouched. It took twelve fixes, all found only by running against a real host.
- **T052** is captured: drift preserved with a retryable record, incumbent-down reported
  incomplete, normal cleanup complete and repeatable, foreign routes healthy throughout.
- **T075 (remainder)** — a Linux run of `default-provider.md` plus a
  `./sb domains use <adapter>` round trip in both directions.

## Rules for adding evidence here

Live proof must use `./sb`, show before/after host state, and state plainly what it does
NOT cover. A file that omits its gaps is worse than a missing file: it reads as coverage.
