# Implementation Plan: Tailnet-Only Hostnames

**Branch**: `feat/host-tailnet-name` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/078-host-tailnet-name/spec.md`

## Summary

Add `sb host tailnet-name add|list|renew|remove`, which publishes an app on a
remote under a tailnet-only HTTPS name. A DNS-only Cloudflare A record points
the name at the remote's tailnet IPv4. A DNS-01 certificate is issued by
`lego` on the controller from a CSR made on the remote, so the key stays there.
The nginx front door's existing route transaction installs a block listening
only on the tailnet address. In-process TLS probes verify it. Both Cloudflare
consumers (a shipped stdlib child and `lego`) receive the token only through
the secrets broker. Every step checks itself first, so reruns finish
interrupted work.

## Technical Context

**Language/Version**: Python 3.12 (controller CLI); Python 3 and `openssl` on
the remote (already required by the nginx front door)

**Primary Dependencies**: standard library; external `lego` v5 binary on the
controller; nginx on the remote

**Storage**: `$SANDBOX_HOME/runtime/tailnet-names/<remote>.json` (new) and
`<remote>.lock`; `lego` data under `$SANDBOX_HOME/runtime/tailnet-names/acme/`
(0700)

**Testing**: `unittest` modules under `tests/`, run only on `xspeed-dev` through
`sb job-start` by Haiku helpers

**Target Platform**: macOS/Linux controller; Linux remote with the nginx front door

**Project Type**: CLI

**Performance Goals**: `add` in under five minutes, dominated by DNS-01
propagation; reruns of a published name in seconds

**Constraints**: the token reaches only broker children; never a
`default_server`; refuse instead of waiting when busy; nothing real in the
repo (placeholders only)

**Scale/Scope**: a handful of names per remote; about 1,200 lines of code
plus tests and docs

## Constitution Check

| Principle | Status |
|---|---|
| I. Per-project instance model | Not affected (remote host routes). |
| II. Registry as source of truth | Reads remotes only through `_remote.get_remote`; the new state file is owned by its module. |
| III. Single entry file, modular package | New package `sandbox/hosting/tailnet_names/` and `sandbox/commands/host_tailnet_name.py`; `sb` untouched. |
| IV. Live-stack verification | Automated proof uses fakes; a live run on a throwaway name needs the orchestrator's approval (brief: never touch xspeed-dev's live nginx, DNS or certificates while developing). Recorded as an open handoff item. |
| V. Idempotency, docs with code | Every step is check-then-act; docs and both `sandbox-cli` skill copies change in the same commit. |
| VI. Parity before removal | Nothing removed or disabled. |
| Module boundaries (CLAUDE.md) | Adds a `TAILNET_NAMES` front-door capability through the manifest, checked before side effects; no new consumers of `sandbox_core.py`, `COMMANDS`, the hermes facade or MCP helpers; no new MCP tool. |
| Secrets (CLAUDE.md) | Broker-only token path (research R2); `sandbox.local.yml` holds names only. |

Gate: PASS. Re-checked after Phase 1: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/078-host-tailnet-name/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/host-tailnet-name-cli.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/tailnet_names/__init__.py
sandbox/hosting/tailnet_names/models.py           # types, parsing, codes, TailnetNameError
sandbox/hosting/tailnet_names/render.py           # nginx block (pure)
sandbox/hosting/tailnet_names/cloudflare_child.py # stdlib child run by the broker
sandbox/hosting/tailnet_names/remote_program.py   # self-contained remote program + controller wrapper
sandbox/hosting/tailnet_names/broker.py           # DNS child and lego through SecretService
sandbox/hosting/tailnet_names/verify.py           # TLS probes
sandbox/hosting/tailnet_names/state.py            # controller records + per-remote lock
sandbox/hosting/tailnet_names/service.py          # add/list/renew/remove over injected Deps
sandbox/commands/host_tailnet_name.py             # CLI glue and envelope
sandbox/hosting/front_door/__init__.py            # + TAILNET_NAMES capability (nginx only)
sandbox/commands/hosting.py                       # dispatch `tailnet-name` (3 lines)
sandbox/cli.py                                    # action, sub-actions, FQDN positional, flags
tests/test_host_tailnet_name.py                   # models, render, service, CLI
tests/test_tailnet_name_cloudflare_child.py
tests/test_tailnet_name_remote_program.py
docs/remote-hosting.md, docs/sandbox-config-reference.md
skills/sandbox-cli/SKILL.md, .agents/skills/sandbox-cli/SKILL.md
```

**Structure Decision**: The library is in `sandbox/hosting/tailnet_names/`,
with every side effect behind a `Deps` dataclass (remote program, nginx route
apply/remove, preflight, broker DNS, broker lego, job query, probes, clock), so
the service is tested with fakes. The CLI glue sits in its own command module.
The shared `hosting.py` and `cli.py` get only dispatch and argument lines.

## Design

1. **Parse at the boundary** (`models.py`): `TailnetNameConfig.from_local_yaml`,
   `TailnetName.parse(fqdn, config)`, `Upstream.parse`, `RemoteTarget.resolve`.
   Each raises `TailnetNameError(code, message)`, a subclass of
   `HostingRefusal` (so it has `.code`), before any network call.
2. **Remote program** (`remote_program.py`): one stdlib program, run through
   the nginx front door's `_python_program` pattern (`sudo -n python3 -c`, JSON
   on stdin, last stdout line JSON). Actions:
   - `inspect {fqdn?, tailnet_ip}`: whether the tailnet IP is bound
     (`ip -4 -o addr`), the held locks among `APPLY_TRANSACTION_LOCKS`, and
     inventory rows (data-model `RemoteInventoryEntry`) for one name or all
     `sandbox-tailnet-*.conf` files.
   - `csr {fqdn}`: create the cert dir, generate `privkey.pem` (EC P-256, 0600)
     if absent, and return a CSR (`CN` and `subjectAltName` = fqdn).
   - `install {fqdn, fullchain, reload}`: check the leaf (SAN has fqdn, key
     matches, not expired), then under the edge lock install `fullchain.pem`
     (0644). When `reload` is set: `nginx -t`, reload, restore and re-reload
     on failure. Returns the expiry and the fingerprint.
   - `remove_cert {fqdn}`: delete the cert dir (exact path only).
   The lock list and paths are injected with `%r` from the controller
   constants, and a test keeps them in lockstep with `host_guard`.
3. **Broker** (`broker.py`): `dns(config, request) -> DnsResult` runs
   `[sys.executable, "-I", cloudflare_child.__file__, json]`;
   `issue(config, fqdn, csr_pem) -> str` writes the CSR under the lego dir,
   clears old outputs, runs `lego run --accept-tos --email E --server S --dns
   cloudflare --csr F --path P` (900 s), and reads `certificates/<fqdn>.crt`.
   Both call `SecretService.run(..., destination="CLOUDFLARE_DNS_API_TOKEN")`.
   `SecretBrokerError` maps to `tailnet_name_secret_unavailable`. Only the
   broker's redacted output tail (6 lines) may appear in a failure detail.
4. **Cloudflare child**: GET `/zones?name=<zone>` (exactly one), GET
   `/zones/<id>/dns_records?type=A&name=<fqdn>`; `ensure` POSTs only when
   there are none; `delete` re-reads the record by id and DELETEs only if name,
   content and `proxied: false` match. Prints one JSON line; never prints
   headers or the token.
5. **Service** (`service.py`), `add`:
   validate → capability → per-remote controller lock → preflight
   (`nginx.preflight`, `inspect` with bound address) → gate → upstream-differs
   check against the remote header → DNS → certificate (skip when the remote
   cert is valid for the name, matches the key and has more than 30 days left)
   → install (`reload` only if the block exists) → gate → server block
   (`nginx.apply_route`; an unchanged digest is a no-op) → verify tailnet →
   verify public (exposure: `nginx.remove_route`, then fail) → record.
   State is written after each step.
   `renew`: due check → gate → CSR → issue → install with `reload=True` →
   verify. `remove`: gate → `nginx.remove_route` → `remove_cert` → DNS delete
   by recorded id → drop the record. `list`: `inspect` of all names merged
   with the state, with drift.
6. **CLI** (`commands/host_tailnet_name.py`): builds `Deps` from the real
   modules, runs the action and prints the envelope (JSON or a short text
   table). Every output path goes through `redact_structure` as defence in
   depth.

## Complexity Tracking

| Choice | Why needed | Simpler alternative rejected because |
|---|---|---|
| External `lego` binary | DNS-01 needs ACME JWS signing; the stdlib has no ECDSA | A new crypto dependency in the shipped CLI is a bigger trust surface |
| Separate Cloudflare child | The token may only reach a broker child | Reusing `_cloudflare.Client` would load the token into the Sandbox process |
| Own renewal transaction | `apply_route` no-ops on an unchanged block, so it would not reload a new chain | Touching the block to force a reload would rewrite a file that did not change |
