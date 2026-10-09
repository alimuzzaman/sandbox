# Implementation Plan: Transactional Edge and DNS Changes

**Branch**: `latest` (feature dir `064-edge-dns-change-transactions`) | **Date**: 2026-10-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/064-edge-dns-change-transactions/spec.md`

## Summary

Make the hosted apply's edge step one ownership-checked, durably journaled
transaction with propagation-aware verification. A new package,
`sandbox/edge_txn/`, does the following:

1. **Desired state.** It computes the desired zone state per declared
   hostname from the validated declaration and the remote's recorded origin
   addresses, including absence for a missing address family.
2. **Classification.** It classifies every live record of the hostname from
   the provider listing, using marker, type, read-only/provider-managed flags
   and recorded previews, into create, update, remove, adopt/adoptable,
   left_alone, drifted or refused.
3. **Journaling.** It journals each change (prior state, written state) to an
   fsync'd file on the controller before calling the provider.
4. **Conditional rollback.** It rolls back in reverse order. Each item is
   restored only if live state still equals what was written and no
   dependent later state exists; otherwise the item is `rollback_conflict`.
5. **Verification.** It verifies through the zone's authoritative name
   servers and connects to each answered address with SNI. Results are
   classified as propagation, real failure or success under one per-hostname
   budget.

`sandbox/commands/hosting.py` keeps orchestration. Its two in-memory
`rollback()` closures (the full apply and the edge continuation) and
`_verify_edge` are replaced by calls into the package. The Caddy and nginx
edge adapters are ordered inside the transaction and report their own
rollback result unchanged. Provision gains a proxied-control-endpoint check
that uses the apply-time control client.

## Technical Context

**Language/Version**: Python 3.12+ (CLI venv)

**Primary Dependencies**:
- Standard library: `ipaddress`, `socket`, `ssl`, `http.client`, `json`, `os.fsync`.
- Existing `sandbox.core._cloudflare.Client`, which gains `list_records(zone_id, hostname)` with full fields (`comment`, `meta.read_only`, `proxied`, `ttl`, `modified_on`), `create_record`, `delete_record`, `update_record` and `zone_nameservers`.
- Existing `_CaddyHostEdge` and `_NginxHostEdge`.

**Storage**:
- Controller: `$SANDBOX_HOME/runtime/edge-journals/<remote>/<project>-<environment>/<transaction_id>.jsonl`.
  - Directory 0700, files 0600.
  - Append-only, fsync after every entry.
  - Retained with the delivery outcome (spec 054) by transaction id.
- Recorded previews come from the existing preview state.

**Testing**:
- `unittest` through `./sb selftest`, plus `tests.test_architecture_boundaries`.
- A fake provider (records, read-only flags, failures injected per call, `modified_on`) and a fake DNS/edge server (local sockets that answer 530 for N seconds, then 200, plus 404 and 401 modes, and an origin-certificate mode).
- Live proof uses a disposable test zone.

**Target Platform**: macOS/Linux controller; Cloudflare as the provider

**Project Type**: CLI + MCP server (single project)

**Performance Goals**:
- The mutation step plus any rollback under a re-acquired lease completes within 60 s (the feature 060 bound).
- The verification budget defaults to 300 s per hostname, stated in the result.
- Real failures confirm in 3 attempts at least 5 s apart.

**Constraints**:
- Never change, remove or treat as conflict a record of an uncovered type, except an owned record whose type was changed.
- Never write a CNAME or rewrite CNAME content.
- Never trust an old journal over a live re-read.
- Never use the system resolver for proxied verification.
- No secret-shaped values in any result.

**Scale/Scope**: up to 32 declared hostnames per target; journals bounded at 512 entries; each result bounded at 64 KiB

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I. Per-project instance model | Pass | The ownership marker is per target (project/environment) |
| II. Registry is the source of truth | Pass | The journal is new controller state registered through the state contract (FR-033), read only via `sandbox/edge_txn/journal.py`. Origin addresses come from the remote registration |
| III. Single entry, modular package | Pass | New `sandbox/edge_txn/` package (desired, ownership, journal, executor, verify, provision_check). `hosting.py` only orchestrates |
| IV. Live-stack proof | Pass (planned) | Quickstart uses a disposable zone and remote. Production zones (xspeed-hub, Lenzora) only with owner approval |
| V. Idempotency, docs with code | Pass | Re-applying the same desired state is a no-op plan. `docs/remote-hosting.md` (DNS, rollback, verification, adoption), CLAUDE.md and CHANGELOG land with the code |
| VI. Parity before removal | Pass | Replacing the in-memory rollback and `_verify_edge` requires parity tests first. Today's status meanings and the edge adapters' transactions are preserved |

Re-check after design: unchanged. The control-protocol shape guard (061
FR-006) is unaffected, because nothing new crosses the controller-to-runtime
transport. The journal is controller-local, and provider calls go
controller-to-Cloudflare.

## Project Structure

### Documentation (this feature)

```text
specs/064-edge-dns-change-transactions/
├── prd.md, spec.md, plan.md, research.md, data-model.md, quickstart.md
├── contracts/edge-transaction.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/edge_txn/
├── __init__.py
├── desired.py         # desired zone state per hostname (families, redirect, wildcard, TTL, proxied, SSL mode)
├── ownership.py       # marker format/parse; classify live records → plan items with reasons
├── plan.py            # plan assembly, verdict (would_apply / would_refuse), leftover reporting
├── journal.py         # durable fsync'd JSONL journal; open/append/replay; leftovers query
├── executor.py        # forward apply under lease; conditional reverse rollback; lease re-acquire hook
├── verify.py          # authoritative resolution, per-address SNI probe, classification, shared budget
└── provision_check.py # proxied control endpoint check with the apply-time client
sandbox/core/_cloudflare.py       # full-field record listing, create/update/delete by id, nameservers; marker comment
sandbox/commands/hosting.py       # plan/apply/edge-continuation call edge_txn; --adopt-records; results
sandbox/commands/remote.py        # provision runs provision_check for proxied control hostnames
sandbox/cli.py                    # --adopt-records
sandbox/preview*                  # preview marker distinct from hosting ownership
docs/remote-hosting.md, CLAUDE.md, CHANGELOG.md
tests/test_edge_txn_desired.py, tests/test_edge_txn_ownership.py, tests/test_edge_txn_journal.py,
tests/test_edge_txn_executor.py, tests/test_edge_txn_verify.py, tests/test_edge_txn_provision.py,
tests/test_edge_txn_hosting_parity.py
```

**Structure Decision**: one package owns zone state, ownership, journal and
verification. Hosting keeps its orchestration and its Caddy/nginx adapters.
Feature 060's lease is consumed through a two-method seam (`acquire(bound)`,
`release()`). Until 060 lands, the seam is backed by today's host-global edge
lock with the same 60-second bound.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| A hand-rolled minimal DNS query (UDP, A/AAAA) to authoritative name servers | Verification must never use the controller's resolver (FR-025), and the CLI venv has no DNS library | DNS-over-HTTPS to a public resolver is itself a caching resolver and would repeat the stale-answer failure. Adding `dnspython` adds a dependency to the CLI venv for about 80 lines of standard library code |
| The journal lives on the controller, not the remote | The edge step is a controller-to-provider call. The journal must survive a controller crash between a provider call and the next step, and the remote may be unreachable while the provider is reachable | A remote journal would turn every provider call into two network hops, and a remote outage would block rollback of provider state |
