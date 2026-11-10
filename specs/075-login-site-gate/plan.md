# Implementation Plan: Login service site gate

**Branch**: `feat/login-site-gate` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/075-login-site-gate/spec.md`

## Summary

Add a third login-service entry mode, `site_gate`. The existing `sandbox-login`
container gains share links: fragment tokens, multi-use slots, server-side sessions
on its tmpfs, and a session-gated reverse proxy (HTTP and websocket) to one declared
upstream. `sb host login-url` mints, lists and revokes them. A minted link is written
only to the controlling terminal. See [research.md](research.md) for the decisions.

## Technical Context

**Language/Version**: Python 3.11+ on the controller; the runtime is stdlib-only Python 3 in the pinned service image

**Primary Dependencies**: none new (stdlib `http.server`, `http.client`, `socket`, `select`)

**Storage**: tmpfs files in the `sandbox-login` container (`/run/sandbox-login/sites/…`); nothing new in host state

**Testing**: `unittest` (`tests/test_host_login_service.py`, `tests/test_login_service_runtime.py`), run on the remote through `sb job-start` in Haiku helpers

**Target Platform**: hosted Compose targets on a Sandbox remote

**Project Type**: CLI plus a container runtime injected through the Compose override

**Performance Goals**: mockup browsing; a file read per gated request is acceptable

**Constraints**: the link value never leaves the operator's terminal; no open proxy; existing modes unchanged

**Scale/Scope**: up to 32 live links and 20 uses per link, for each entry

## Constitution Check

| Principle | Status |
|---|---|
| I. Per-project instances | N/A (hosting targets; no instance resolution change) |
| II. Registry as source of truth | N/A (registry untouched) |
| III. Single entry file, modular package | Pass. The code goes in `sandbox/hosting/login_service/` and `sandbox/commands/hosting.py`; `sb` is unchanged |
| IV. Live-stack verification | Partial. Unit and runtime suites run on the remote; the live target check belongs to the parent session (no `host apply` here, by the owner's limit). Recorded as an unresolved limit |
| V. Idempotency, docs with code | Pass. Mint is a new link each time by design; list is read-only; revoking an id that is gone reports `site_link_unknown`. `docs/login-service.md` and both `sandbox-cli` skills are updated in the same change |
| VI. Parity before removal | Pass. Nothing is removed; `inbox_gate` and `magic_link` keep their paths |

Re-checked after design: no violations.

## Project Structure

### Documentation (this feature)

```text
specs/075-login-site-gate/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/cli.md
├── contracts/gate-http.md
└── tasks.md
```

### Source Code

```text
sandbox/hosting/login_service/__init__.py   # parse site_gate, optional capture, render, preflight, site_command
sandbox/hosting/login_service/runtime.py    # SiteStore, site link page, session proxy, manage CLI
sandbox/core/_hosting.py                    # refuse basic_auth with a site_gate entry
sandbox/commands/hosting.py                 # site mint/list/revoke in the login-url path; preflight upstreams
sandbox/cli.py                              # --uses, --list, --revoke on the login-url arguments only
tests/test_host_login_service.py
tests/test_login_service_runtime.py
docs/login-service.md
skills/sandbox-cli/SKILL.md
.agents/skills/sandbox-cli/SKILL.md
```

**Structure Decision**: Extend the existing login-service package and the `login-url`
command path. Stay off `host exec`, `host_stack.py`, `ui_dash.py` and the web panel.

## Test procedure

Tests and type-checks run only in Haiku 5.5 helpers, on the remote:

```text
<controller>/sb job-start --remote xspeed-dev --workspace login-site-gate --project-dir <worktree> \
  --request-id <unique> --timeout 900 --wait --json -- sh -c '$HOME/sandbox/sb-src/.cli-venv/bin/python -m unittest <modules> > /tmp/<tag>.log 2>&1; rc=$?; grep -E "^(FAIL|ERROR):|^Ran |^OK|^FAILED" /tmp/<tag>.log; echo PYTHON_EXIT=$rc; exit $rc'
```

Compare any failure with a baseline run of the read-only controller checkout at the
same base, because the server's umask (0002) breaks some suites regardless of code.

## Complexity Tracking

None.
