# Implementation Plan: Hosted share links

**Branch**: `feat/hosted-share-links` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/081-hosted-share-links/spec.md`

## Summary

Extend the 075 `sandbox-login` gate into a share system that agents can drive. A
`share:` block in a hosting environment declares a gate hostname, `private`, and the
agent opt-in. Sandbox now routes gate hostnames itself: `sandbox-login` publishes one
managed loopback port (the target's port + 3000), and both front doors send gate
hostnames there. With `private`, every other served hostname answers 404 at the front
door. Links follow the Agent Notes model: 7 days by default, 30 at most, optional use
limit, path scope, token-for-cookie swap, revoke ends sessions. The URL is
`https://<gate>/s/<128-bit id>#<256-bit token>`. A new `sb host share
create|list|revoke` command (own module) and an MCP `share` tool group return the link
once and audit every action. Both front doors add a deferred
`X-Robots-Tag: noindex, nofollow, noarchive` to every hosted response unless
`indexing: public`, and `robots` defaults to deny. A stack link opens a gate-served
page listing the stack's hostnames, health and per-view single-use sign-in links: an
HMAC-keyed WordPress autologin, and local login-service entries.

## Technical Context

**Language/Version**: Python 3.11+ on the controller. The gate runtime is stdlib-only
Python 3.13 in the pinned image. The WordPress mu-plugin is PHP 7.4+.

**Primary Dependencies**: None new. Caddy and nginx front doors, Docker Compose.

**Storage**: Gate tmpfs (`/run/sandbox-login/sites/<entry>/`) for links, use markers,
sessions and stack records. Controller audit at
`$SANDBOX_HOME/runtime/share-links/audit.jsonl`; server audit at
`<remote SANDBOX_HOME>/runtime/share-links/audit.jsonl` (both 0600, append-only).

**Testing**: pytest (`tests/test_host_share.py`, `tests/test_share_gate_runtime.py`,
`tests/test_hosted_noindex.py`, plus updates to `tests/test_login_service_runtime.py`
and `tests/test_host_login_service.py`). Runs on xspeed-dev through
`sb job-start --remote xspeed-dev`, compared with an `origin/latest` baseline.

**Target Platform**: Linux remotes with a Caddy or nginx front door.

**Project Type**: CLI plus MCP server plus an injected container runtime.

**Performance Goals**: The gate forwards a development site; no throughput target
beyond 075. Stack-page health probes are bounded: 3 s each, 16 hosts at most, cached
for 30 s.

**Constraints**: No token in any log, state, audit, argv or job output. Stay off files
owned by parallel threads (panel, `host_stack.py`, hosting job paths,
`sandbox/resources/*`, `sandbox/hosting/tailnet_names/*`, and the listed tests).

**Scale/Scope**: At most 32 live links and 512 sessions per gate entry (075 caps).

## Constitution Check

| Principle | Status |
|---|---|
| I. Per-project instances | Unaffected: hosted targets only. |
| II. Registry is truth | Stack lookups go through `stack_registry` and `target_resolution`, never raw JSON. |
| III. Single entry, modular package | New command module `sandbox/commands/host_share.py`, core in `sandbox/hosting/share/`, CLI registered through the existing `host` spec, and an MCP group through `tools/manifest.py`. |
| IV. Live verification | The gate runtime is proven over real sockets in-process. A live apply on xspeed-dev is out of bounds (never touch live targets). Recorded as a limit; the orchestrator verifies on the mockups rollout. |
| V. Idempotency, docs with code | Mint, list and revoke are retry-safe (an id collision is refused, revoke of an absent id is a named refusal). `docs/login-service.md`, `docs/remote-hosting.md`, CLAUDE.md and the sandbox-cli skill land with the code. |
| VI. Parity before removal | `host login-url` stays. 075 `site_gate` keeps working through the new store. Front-door routing for gate hostnames is additive; non-gate routes are unchanged unless `private`. The `robots` default flip is the owner's explicit policy change. |

Gate result: PASS. No unjustified violation.

## Project Structure

### Documentation (this feature)

```text
specs/081-hosted-share-links/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   ├── manifest.md
│   └── gate-http.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/share/__init__.py        # types, parsing of `share:`, link/scope rules, audit
sandbox/hosting/share/stack_page.py      # stack page record and WordPress key mu-plugin
sandbox/hosting/login_service/__init__.py  # declaration merge, render with published port, preflight
sandbox/hosting/login_service/runtime.py   # share store v2, /s/<id>, scope, stack page, noindex
sandbox/core/_hosting.py                 # validate `share`/`indexing`, override port, Caddy routing + header
sandbox/hosting/front_door/nginx.py      # gate routing, private 404, header
sandbox/commands/host_share.py           # `sb host share create|list|revoke`
sandbox/commands/hosting.py              # dispatch only, plus the 075 site-link path on the new store
sandbox/cli.py                           # `share` action, verbs and options
mcp/wp-server/tools/share.py             # MCP tools
mcp/wp-server/tools/manifest.py          # `share` group
docs/login-service.md, docs/remote-hosting.md, CLAUDE.md
tests/test_host_share.py, tests/test_share_gate_runtime.py, tests/test_hosted_noindex.py
```

**Structure Decision**: The share types and rules live in `sandbox/hosting/share/`,
and the gate in the existing runtime file, which must stay one stdlib file because it
is injected into the container. `hosting.py` only dispatches to the new command.

## Complexity Tracking

| Choice | Why needed | Simpler alternative rejected because |
|---|---|---|
| A second managed loopback port for the gate | Front doors have one upstream per target; the gate must answer gate hostnames without a project edge | Proxying all traffic through the gate puts a Python proxy in front of every public site |
| HMAC key in a WordPress mu-plugin | Per-view sign-in links must be minted by the gate, which cannot write into the WordPress container | A controller round trip per view needs the controller online; giving the gate Docker access is a privilege escalation |
