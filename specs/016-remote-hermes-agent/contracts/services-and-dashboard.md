# Service and Dashboard Contract

## Gateway Service (V1)

Sandbox manages a profile-scoped systemd unit that executes the pinned upstream launcher as the existing remote Sandbox account.

Required properties:

- `WorkingDirectory` is the validated managed repository root or explicitly configured gateway working repository.
- `Environment` includes only non-secret paths/profile selectors. Secret values live in an owner-readable environment file outside the repository.
- `ExecStart` uses the absolute Hermes launcher and upstream gateway foreground/run form supported by the pinned release.
- Restart policy is bounded and avoids rapid permanent loops.
- Standard output/error go to journald with retrieval limits and integration redaction.
- Install validates the unit before enabling it.
- Start/restart validates a non-empty, non-wildcard allowlist.
- Stop affects Hermes gateway only and does not stop WordPress instances or the remote Sandbox MCP service.

## Dashboard Service (V3 after V2)

Default service command shape:

```text
<absolute-hermes> dashboard
  --host 127.0.0.1
  --port 9119
  --no-open
  --tui
```

The final flags are checked against the pinned upstream release during implementation. Sandbox never passes `--insecure`.

Required properties:

- Same remote operating-system user, `HERMES_HOME`, and selected profile as CLI/gateway execution.
- Dedicated systemd unit and bounded restart behavior.
- Loopback listener by default, with a port-availability preflight.
- Health probe verifies the dashboard process and expected authentication/access mode without logging cookies or tokens.
- No V3 unit or dependency mutation before the current V2 gate passes.

## Default Dashboard Access

The supported default is SSH forwarding:

```text
ssh -N -L 9119:127.0.0.1:9119 <configured-remote>
```

The CLI prints a redacted instruction derived from the configured remote but does not reveal stored credentials. SSH authentication and loopback binding are the access boundary. The dashboard must not bind a public address in this mode.

## Optional Public Dashboard Exposure

Preconditions:

1. Current V2 gate is passed.
2. Feature 015 managed hosting is present and its validation/plan/apply/rollback contract is available.
3. An explicit normalized FQDN is provided.
4. Supported upstream OAuth client configuration is stored outside version control.
5. A read-only plan has identified route, TLS, authentication, health, and rollback changes.
6. The operator supplies current `--confirm` approval.

Public exposure rules:

- Use upstream authenticated hosted mode; do not substitute `--insecure` or an invented Hermes password store.
- Route only the declared FQDN to the loopback dashboard through the managed proxy/TLS layer.
- Preserve unrelated DNS and Caddy configuration.
- Verify an unauthenticated request is rejected and an authenticated probe succeeds.
- If either probe, route reload, certificate, DNS, or dashboard health fails, restore the previous managed route/DNS state.
- `unexpose` removes only integration-owned route/DNS state and returns the dashboard to SSH-forward-only mode.

## Service State Results

Every service status returns:

```json
{
  "installed": true,
  "enabled": true,
  "active": true,
  "substate": "running",
  "pid": 1234,
  "last_health": "healthy",
  "last_checked_at": "2026-07-10T12:00:00Z"
}
```

Raw unit environment, command arguments containing secrets, journal fields outside the allowlist, cgroup internals, and SSH connection strings are excluded.

## Rollback Matrix

| Operation | Failure point | Required rollback |
|---|---|---|
| Gateway install | Unit validation/install | Restore prior unit and enabled/active state |
| Gateway restart | New process unhealthy | Restore prior config and restart previous service if it was active |
| Dashboard install | Dependency or compatibility check | Restore previous Hermes environment; gateway/CLI remain available |
| Dashboard start | Bind/health failure | Stop new service and preserve prior config |
| Dashboard expose | DNS/TLS/proxy/auth/health failure | Restore prior managed route/DNS, stop unsafe public listener, retain loopback service when safe |
| Dashboard unexpose | Route removal failure | Keep prior known-good public route rather than leaving partial routing; report manual action |
