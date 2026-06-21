# Implementation Plan: Snapshot & Restore from the WordPress Dashboard

**Branch**: `cwd-instance-resolution` (worktree) | **Date**: 2026-06-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-dashboard-snapshots/spec.md`

## Summary

Let developers take/restore/list/delete snapshots from wp-admin. A sandbox-only mu-plugin
renders a Tools screen and calls scoped routes on the **`sb web` host server**
(`/api/instance/<inst>/snapshot|restore|snapshots`), authenticated by a per-instance
`bridge_token`. The server runs the existing `sb snapshot`/`sb restore` **out-of-band** on
the host (so a restore never severs the serving request's own DB) and the mu-plugin polls
for completion. Bridge URL + token + instance are injected into the mu-plugin at provision
time. Docker-backed instances only in v1; Herd surfaces an unsupported notice.

## Technical Context

**Language/Version**: PHP (mu-plugin, target WP's supported PHP) + Python 3 (the `sb web`
server, stdlib `http.server`) + JS (mu-plugin admin page; vanilla, no build).

**Primary Dependencies**: existing CLI `cmd_snapshot`/`cmd_restore`/`cmd_snapshots` (`sb`
~L3223-3313); the `sb web` server (`cmd_web`) + its `/api/*` handlers; the mu-plugin
writers `_write_mail_muplugin` (~L1031) / `_write_ssl_muplugin` (~L1010) /
`_autologin_mu_plugin` (~L2065) as the provisioning pattern; `sandbox.local.yml`
`instances:` for the token; `host.docker.internal` for container→host reachability.

**Storage**: snapshots unchanged — `runtime/snapshots/<instance>/<name>/` (`db.sql`,
`uploads.tgz`, `META`). New: `instances.<name>.bridge_token` in `sandbox.local.yml`.

**Testing**: live verification — take/restore from wp-admin and cross-check with
`sb snapshots`; auth-rejection probe; herd-unsupported probe (constitution IV).

**Target Platform**: macOS/Linux dev machines; Docker Desktop / Docker Engine.

**Project Type**: WordPress mu-plugin + host CLI/web-server extension.

**Performance Goals**: capture/restore are bounded by DB+uploads size; treated as
long-running async ops (out-of-band) with polling — no fixed latency target (low impact,
deferred from clarify).

**Constraints**: mu-plugin runs INSIDE the WP container (Docker mode) — no `sb`, no Docker
socket, no host FS; all host work goes through the authenticated bridge. Restore out-of-band.
Sandbox-only (never affects a real site). Depends on spec 001's per-project model.

**Scale/Scope**: one admin screen, one mu-plugin, ~4 new `sb web` routes, token plumbing in
provisioning, auto-start of `sb web` from `sb up`/`ensure`.

## Constitution Check

- **I. Per-project only** — the bridge routes are scoped to one resolved instance; the token
  is per-instance. PASS.
- **II. Registry/local.yml source of truth** — `bridge_token` lives in `sandbox.local.yml`
  `instances.<name>`; the server resolves the instance the same way the CLI does. PASS.
- **III. Single entry file / modular** — host changes land in the `sb web` server (which
  becomes `sandbox/commands/ui_dash.py` after spec 001 Stage C); no new entry point. PASS.
- **IV. Live-stack verification** — quickstart.md defines wp-admin + CLI cross-checks. PASS.
- **V. Idempotency + docs-with-code** — provisioning re-writes the mu-plugin + token
  idempotently; CLAUDE.md gotcha (mu-plugin list) updated with the change. PASS.
- **VI. Parity before removal** — additive feature; no removal. N/A but compatible.
- **Plugin-code non-negotiables** (CLAUDE.md) — auth (nonce + capability) on the admin
  handlers, sanitize-in/escape-out, prefix everything `sandbox_*`, WP APIs
  (`wp_remote_post`, not curl). Enforced via FR-004/FR-008 and the contracts.

No violations → Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-dashboard-snapshots/
├── spec.md           # done (+ clarifications)
├── plan.md           # this file
├── research.md       # Phase 0 — decisions (this command)
├── data-model.md     # Phase 1 entities (this command)
├── contracts/
│   └── bridge-api.md # Phase 1 — the 4 scoped routes (this command)
├── quickstart.md     # Phase 1 — live validation guide (this command)
└── tasks.md          # /speckit-tasks
```

### Source Code (repository root)

```text
sb
  ├─ cmd_web (+ /api/instance/<inst>/snapshot|restore|snapshots routes)   # bridge host
  ├─ _write_snapshot_muplugin(instance, token, url)  # NEW, mirrors _write_mail_muplugin
  ├─ provisioning: mint + persist instances.<name>.bridge_token; write mu-plugin
  ├─ cmd_up / ensure_instance: idempotently start/refresh `sb web`
  └─ cmd_snapshot/restore/snapshots                                       # reused as-is
runtime/wp-<instance>/wp-content/mu-plugins/
  └─ 00-sandbox-snapshots.php   # NEW admin UI + bridge client (generated)
# after spec 001 Stage C, the host side lives in sandbox/commands/ui_dash.py +
# sandbox/core/provision.py — same logic, modular home.
```

**Structure Decision**: extend existing surfaces (no new daemon, no new entry point) per the
clarified bridge decision. The mu-plugin is generated by provisioning exactly like the
mail/ssl/autologin mu-plugins so it survives container restarts and recreation.

## Phase 0 — Research (→ research.md)

Resolved decisions (from `/speckit-clarify`): host server = `sb web`; auth = per-instance
`bridge_token`; URL/token/instance injected at provision; `sb up`/`ensure` auto-starts
`sb web`; restore out-of-band; v1 docker-only. Open research items to record: (a)
container→host reachability for `sb web` across Docker Desktop macOS vs Docker Engine Linux
(bind address + `host.docker.internal` / `--add-host`), (b) how `sb web` currently binds
(127.0.0.1:8765) and what must change to be container-reachable while staying token-gated,
(c) async job model for out-of-band restore + polling (job id + status file under
`runtime/`), (d) herd-unsupported detection reuse (`_is_herd_instance`).

## Phase 1 — Design & Contracts

- **data-model.md**: Snapshot, Snapshot mu-plugin, Host bridge route, Bridge token, Bridge
  job (async restore/capture status), with fields, validation, and state transitions.
- **contracts/bridge-api.md**: the four scoped routes — request/response/status/error
  shapes, auth header, and the verb→`sb` mapping. CLI command schema for any new flags.
- **quickstart.md**: runnable validation (take from wp-admin → see in `sb snapshots`;
  mutate → restore → verify; auth-rejection probe; herd notice).
- **Agent context**: update the `<!-- SPECKIT START -->` block in `CLAUDE.md` to reference
  this plan.

## Complexity Tracking

No constitution violations — none required.
