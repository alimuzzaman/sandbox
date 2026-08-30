# Host ingress evidence index

Live evidence exists for the default provider and for every read-only surface, on macOS.
The Ubuntu system-Caddy lifecycle is also captured through the single-invocation conformance
harness. Source now qualifies only Linux system Caddy exact HTTP against that fixed evidence;
no runtime proof input can promote or widen it. Normal live Linux `./sb domains use
system-caddy` adoption has not yet been recaptured, so T078 remains open.

| Evidence | State | Covers | Still required |
|---|---|---|---|
| `unit-gate.md` | complete | focused contract and security tests | — |
| `default-provider.md` | live (macOS + Ubuntu) | T075: default ingress serves clean URLs; Linux conflict report and provider round trip | a Linux host with free :80/:443 |
| `listeners.md` | live (macOS) | T026: free/exact/wildcard/Sandbox-owned/foreign classification, non-mutation | Linux `/proc`+`ss` observer; IPv6 dual-stack |
| `support-and-consent.md` | live (macOS + Ubuntu) | T063: tiers, pin precedence, full consent lifecycle, credential-pending, detect-only | NPM credential storage |
| `compatibility.md` | live (macOS) | T069: Sandbox Caddy + per-port parity, corrected conflict diagnosis | Linux parity |
| `quickstart-run.md` | live (macOS + Ubuntu) | T068: read-only baseline, clean-URL serve, repeat-safety; lifecycle in `system-caddy.md` | the live transaction-failure matrix |
| `cleanup.md` | live (Ubuntu + macOS) | T052: normal, repeated, drift, incumbent-unavailable cleanup with foreign routes healthy | incumbent replaced by another product |
| `system-caddy.md` | live (Ubuntu 24.04) | T044: add/request/update/remove through a real incumbent, incumbent routes preserved | HTTPS/wildcard; the live transaction-failure matrix |

The locally tested T078 portion fixes the production registry qualification to
`037-t044-ubuntu-2404` and requires Linux, exact HTTP, observed Caddy identity, proven
listener ownership, and helper/import readiness. The read-only preflight binds the selected
PID/start/executable digest/socket set/listen endpoint to active `caddy.service`; it refuses
foreign or second-process collisions and owner drift before DNS mutation. This is source
validation, not new live CLI evidence.

## What each open item needs

- **T044** is captured on Ubuntu 24.04: adoption serves HTTP 200 through the incumbent,
  repeats safely, and cleans up completely with the incumbent's 16 pre-existing routes
  untouched. It took twelve fixes, all found only by running against a real host.
- **T052** is captured: drift preserved with a retryable record, incumbent-down reported
  incomplete, normal cleanup complete and repeatable, foreign routes healthy throughout.

## Rules for adding evidence here

Live proof must use `./sb`, show before/after host state, and state plainly what it does
NOT cover. A file that omits its gaps is worse than a missing file: it reads as coverage.
