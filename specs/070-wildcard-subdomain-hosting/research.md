# Research: Wildcard Subdomain Hosting

Evidence is from `latest` at 99e7c12 unless stated. Claims about Cloudflare and nginx behaviour in production are unproven until the quickstart runs.

## R1. Older controllers already refuse

**Decision**: rely on the existing overlap check as the refusal for controllers without this feature, on the nginx front door.

**Rationale**: `_name_covers` treats `*.zone` as covering every name under the zone (`sandbox/hosting/front_door/nginx.py:345`), and `find_conflicts` reports it. An older controller's `preflight` therefore raises `front_door_hostname_conflict` before any remote change, on any apply or control-route configure under the zone (`sandbox/commands/hosting.py:1354`). This meets FR-040 for code that cannot be changed.

**Consequence**: once the fallback is installed, every pre-feature checkout refuses to apply any target under that zone, including existing exact targets, and refuses to reconfigure a control route under it. `~/.local/bin/sb` points at `sandbox-main`, at a7e87e3 from 2026-10-11. Enable's plan must state this consequence, and the owner must decide when to enable knowing it.

**Consequence starts at fallback install**, not at publication, so the owner's approval of that step must say so.

**Limits of older code**: an older destroy removes only the records in its own state (`_remove_cloudflare`) and leaves a stale dependent, which status reports and `--release-dependent` clears. Older `sb remote edge --remove-route` has no guard, and could delete the fallback file while the wildcard is live. Nothing can change that code, so status reports it as `safe: false`.

**Alternatives**: raising the oldest served protocol (host apply does not consult it, and it would block unrelated operations); manifest version 2 (would add a required step to every new site).

## R2. Name eligibility must include subordinate names

**Decision**: read the zone's whole record list, and treat a name as having its own node if any record exists at that name or below it.

**Rationale**: under RFC 4592, any node blocks the wildcard for that name, including an empty non-terminal. `_acme-challenge.x.asb.bd` makes `x.asb.bd` non-covered with no record at `x` itself. `Client.records` queries only the exact name (`sandbox/core/_cloudflare.py:123`). Whether Cloudflare follows this in every case is validated in the quickstart.

**Alternatives**: a query for the exact name only (misses non-terminals); a public DNS probe (subject to caches, which is the original failure).

## R3. Public edge verification without the target's DNS answer

**Decision**: resolve a fresh random label under the zone, for example `sb-verify-<16 hex>.asb.bd`, then open a TLS connection to those addresses with the target's name as SNI and Host, verifying the certificate. Report the target name's own system resolver answer separately, without failing.

**Rationale**: a random label has no cached negative answer, and it reaches Cloudflare through the wildcard. Cloudflare routes by SNI and Host, so this proves edge routing and TLS for the target with no dependence on the target name's cached state. `_verify_edge` today fails on any resolver error for 5 minutes, then rolls back (`sandbox/commands/hosting.py:3982`).

**Failure handling**: an HTTP answer outside the accepted set after the retries is a real edge failure and fails as today. If the random label does not resolve, public edge is `unverified`, and delivery does not fail for that alone (FR-015, FR-016).

**Alternatives**: querying the authoritative nameservers (needs a DNS client and does not prove edge routing); skipping public verification (FR-016 forbids reporting that as verified).

## R4. Fallback route

**Decision**: one file through the existing transaction, name `coverage-<zone>`, so `/etc/nginx/conf.d/sandbox-coverage-<zone>.conf`, with `server_name *.<zone>;` on 80 and 443, a server-level `return 404` with a fixed body, and `X-Robots-Tag: noindex, nofollow` plus a fixed marker header, both with `always`.

**Rationale**: a server-level `return` answers every path and method, with no location to bypass, and nginx drops the body on HEAD. `apply_route` provides validation, reload and restore, but `_probe_host` skips wildcards (`nginx.py:696`), so the fallback needs its own origin probe: loopback HTTPS with a random label, requiring 404 and the marker.

**Multi-level names**: nginx `*.zone` also matches `a.b.zone`, so these get the same 404 if they reach the server, as FR-021 requires.

**Certificate**: `ensure_origin_certificate(entry, "coverage-<zone>", ["*.<zone>"], client)`, a new key path per zone. It returns early when present, so renewal needs a forced path that installs a new certificate only after issue succeeds, and keeps the old one otherwise.

## R5. Overlap exemption

**Decision**: add `is_coverage_fallback_file(path)`, matching exactly `/etc/nginx/conf.d/sandbox-coverage-<valid zone>.conf`. In `find_conflicts`: a declared exact name is not in conflict with a wildcard pair from that file. A declared wildcard still conflicts with it. Installing the fallback uses a separate classifier that permits exact pairs and refuses wildcard and regex pairs.

**Rationale**: `find_conflicts` also runs inside the remote probe program at `nginx.py:479` and `nginx.py:516`, so the exemption must exist in the program text too. The tests must exercise both copies. Because a foreign file with that exact path could only come from root, the file's own marker is checked in status.

**The fallback's own row**: `probe` runs `find_conflicts` for every route, so the fallback's own declared wildcard would list every exact route in the zone, and `summarize` would surface them. The probe must skip exact pairs for the fallback's own row, in both copies, and report them as exceptions instead.

**Limit**: an operator could hand-write that path. Sandbox files are root-owned, and status compares the digest with the store.

## R6. Target decision

**Decision**: a pure function of manifest policy, coverage document, zone listing and target state, returning `exact`, `shared` or a refusal.

| Coverage state for the zone | Target | Result |
|---|---|---|
| remote without the capability | any | `exact`, as today, store not read |
| store read fails | any | refuse `coverage_store_unavailable` before effects; SSH is required for apply anyway |
| name in no listed zone, or `retired` | any | `exact`, as today |
| fallback file present for the zone, no record | any | refuse `coverage_record_missing` |
| any other | `dns: exact` | `exact`, with the drift rule |
| any other | owned exact record in state, or a record equal to desired | `exact`, with the drift rule (FR-012, FR-013) |
| `fallback_only` | `auto` | `exact`, with the drift rule; no shared DNS exists yet |
| active, observed matching | `auto`, single route, proxied strict, eligible | `shared` |
| active | `auto`, otherwise | refuse `coverage_name_ineligible` |
| active, drift or evidence unavailable | `auto`, would be shared | refuse `coverage_drift` |
| enabling, incomplete, disabling | `auto`, would be shared | refuse `coverage_not_active` |
| disabled | known dependent | refuse `coverage_disabled_dependent`; a per-site record needs explicit `dns: exact` |
| disabled | other | `exact`, with the drift rule |

Terms: `auto` is the manifest value, `shared` is the decision, and "covered" means a target whose decision is `shared`.

**Drift rule for exact records (FR-025)**, only in zones with a coverage document: if a record exists at the name and differs from desired, and it does not equal the recorded `applied` snapshot, apply refuses `managed_record_drift` unless `--allow-record-overwrite` is passed. Without a document, today's overwrite stays (FR-018).

**Single-route rule**: a target with more than one route is `exact` if it already has an owned or equal record, and otherwise refuses `coverage_name_ineligible` under `auto`, naming `cloudflare.dns: exact`. Aliases can sit at the apex, in other zones or use wildcards, and a per-route split is not needed for the request.

**Certificate**: a covered target keeps its own origin certificate and its cleanup, with no new code. Zero-write tests are scoped to DNS calls.

**Zone lookup**: `decide` first asks the store for the list of zones, and matches by suffix. A name in no listed zone returns `exact` with no provider call. On a Caddy remote the store is not read at all.

**Threading**: the result is stored as `validated["dns_coverage"]`, which `desired_plan` reads, so every call site at `hosting.py:2431`, `2465` and `7386` agrees. `edge_intent` gains an optional `dns_coverage` key only when shared, so existing intent digests do not change. `validate_edge_intent` already permits extra keys.

## R7. No migrate and no protocol bump

**Decision**: all remote work runs through controller-carried programs and existing SSH transactions, so older-runtime and mid-apply-upgrade cases do not arise (FR-042).

**Rationale**: coordination's program runs this way, and `sb host apply` does not check the control protocol. `sandbox/remote_runtime/shapes.py` must stay green: nothing is added to its fingerprinted modules. A later commit still changes the runtime revision digest, so `sb wp --remote` keeps needing an exact match, as it does for any commit.

## R8. Delivery evidence

**Decision**: add a separate `evidence` object with `origin`, `public_edge` and `resolver` to the host state `edge` block, the apply result and `host status`.

**Limit**: the delivery record's `incomplete` state on ordinary successful applies was seen on 2026-11-05 and filed as feedback. It is a separate defect, and this feature does not change it.

## R9. Live-proof constraint

**Decision**: stage the proof. First install the fallback with no DNS change and probe it at the origin, for an unknown name, an exact Sandbox route, a panel route and the control route. This step already makes older checkouts refuse applies under the zone (R1). Publication then needs the owner's explicit go, because it is production DNS for `asb.bd`.

**Rationale**: no spare zone is known, and a sub-zone name cannot test first-level edge TLS. A wildcard only adds answers for names that return `NXDOMAIN` today, and removal is one record, so the risk of publishing is bounded. Enable therefore has a `--stop-before-publish` flag.
