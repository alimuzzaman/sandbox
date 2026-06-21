# Phase 0 Research: Dashboard Snapshots Bridge

Consolidated decisions for the host bridge. Decisions already locked via `/speckit-clarify`
are recorded here with rationale; the remaining items are resolved with best-practice
defaults to be confirmed during implementation against the live stack.

## D1 — Host server for the bridge

- **Decision**: Serve the snapshot routes from the existing `sb web` dashboard server.
- **Rationale**: It already is a localhost HTTP server that routes by instance and shells
  `sb` (snapshot/status/etc. console). Reuses routing + process model; no new daemon.
- **Alternatives**: MCP wp-server (only up during a Claude session — unreliable for a
  browser-only workflow); a dedicated per-instance listener (the "new daemon" already
  declined).

## D2 — Authentication

- **Decision**: Per-instance random `bridge_token`; mu-plugin sends `Authorization: Bearer
  <token>`; the server accepts only the token matching the resolved instance, else 403.
- **Rationale**: Network isolation alone is insufficient because the listener must be
  reachable from the container (see D5), so it can't be loopback-only. A per-instance secret
  scopes access to exactly one instance and mirrors the proven autologin-token pattern.
- **Alternatives**: loopback-bind only (not reachable from container; weak); forward WP
  nonce/cookie (couples the host server to WP auth, more moving parts).

## D3 — Config delivery to the mu-plugin

- **Decision**: Inject `SANDBOX_BRIDGE_URL`, `SANDBOX_BRIDGE_TOKEN`, `SANDBOX_INSTANCE` as
  PHP constants into the generated mu-plugin at provision time; regenerate on recreate.
- **Rationale**: Same mechanism as the mail/ssl/autologin mu-plugins (token embedded in the
  file so it survives wp-config regeneration on container restart). No runtime discovery.
- **Alternatives**: wp_option (wiped on DB reset/restore — fatal for a restore feature);
  fixed port convention (brittle across hosts/ports).

## D4 — `sb web` lifecycle

- **Decision**: `sb up`/`ensure_instance` start/refresh the `sb web` server idempotently.
- **Rationale**: A browser-only user never runs `sb web` manually; the bridge must be up
  whenever the instance is. Idempotent start fits the existing "safe to re-run" rule.
- **Alternatives**: on-demand with a "run `sb web`" prompt (silent manual step; worse UX).

## D5 — Container → host reachability

- **Decision**: The mu-plugin reaches `sb web` via `host.docker.internal:<port>`. The server
  must bind to an address reachable from containers, not pure loopback.
- **Rationale**: Docker Desktop (macOS/Windows) resolves `host.docker.internal` to the host;
  host-bound services are reachable. On Docker Engine (Linux) `host.docker.internal` needs
  `--add-host=host.docker.internal:host-gateway` (already commonly set) and the server bound
  to the bridge gateway / `0.0.0.0` on the loopback-restricted port.
- **Implementation note**: bind to the host-gateway-reachable interface; the `bridge_token`
  (D2) is the security boundary, not the bind address. Confirm reachability live during
  implementation (curl from inside the WP container to the bridge URL).
- **Alternatives**: docker-socket mount (rejected — container host-root); a unix socket
  bind-mounted into the container (viable but more plumbing than HTTP + token).

## D6 — Out-of-band restore + async job model

- **Decision**: Bridge `restore` (and `snapshot`) spawn `sb restore/snapshot` as a detached
  host process and return a `job_id`; status is written to a job file under `runtime/` and
  exposed via a status route the mu-plugin polls.
- **Rationale**: A restore resets the DB serving the request; running inline would sever the
  request's own connection. Out-of-band + polling is the only safe model and also handles
  long captures without an HTTP timeout.
- **Alternatives**: synchronous request (false-failure on restore; timeout risk).

## D7 — Herd (host) instances

- **Decision**: v1 is Docker-only; on herd instances the dashboard shows the unsupported
  notice. Reuse `_is_herd_instance` (already used by `cmd_snapshot`/`cmd_restore` to refuse).
- **Rationale**: CLI snapshots already refuse on herd; the dashboard must match, not appear
  to work. Herd support follows whenever CLI herd-snapshot support lands.

## D8 — Snapshot name validation

- **Decision**: Reuse the CLI's `^[\w.-]+$` rule; blank name → a generated default
  (timestamp-based) that satisfies the rule.
- **Rationale**: Guarantees dashboard and CLI snapshots are mutually valid/visible (FR-002).
