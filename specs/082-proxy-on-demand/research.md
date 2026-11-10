# Research: 082 proxy on demand

Evidence was collected read-only from this checkout on 2026-11-10. Scout reports are in
the scratch root (`.scratch/sandbox/proxy-on-demand/audit-*.txt`, `map-lifecycle-tests.txt`).

## R1. Why the proxy outlives every instance

- Decision: `restart: "no"` on the proxy service.
- Evidence: `render_proxy_compose()` sets `restart: unless-stopped`; no instance compose
  file sets any restart policy (`grep "restart: " sandbox/core` finds only the proxy).
  After an engine restart only the proxy returns.
- Alternatives: `on-failure` (whether it comes back after a daemon restart depends on
  the container's last exit, so it can still return alone); keep `unless-stopped` and only
  stop when idle (an engine restart after an unclean stop still brings it back).

## R2. Who starts the proxy today

- Finding: `cmd_up` calls no proxy function (comment: "Never invoke the unreceipted
  aggregate proxy on instance up"); `test_up_never_mutates_unreceipted_legacy_proxy_state`
  enforces it. The call was removed in `c043059` (2026-08-02, the retirement checkpoint
  later reversed by `docs/clean-url-default.md`). `_ensure_proxy_up` has no callers.
  Only `ensure --create` (`_secure_at_create` -> `_ensure_url_proxy`) and `domains
  up/setup` start it.
- Decision: `cmd_up` calls `_ensure_proxy_up` for an instance with a clean `.<tld>`
  domain. `_ensure_proxy_up` only acts when the proxy is stopped and the host is already
  provisioned (alias + resolvers), so it needs no sudo and never reloads a running proxy.
  The composed facade (`proxy.plan/apply`) stays uncalled. The test is updated to this
  contract.
- Rationale: with R1 alone, clean URLs would fall back to `localhost:<port>` after every
  engine restart, a parity regression under principle VI.

## R3. What counts as demand

- Decision: any running container of a Compose project named `sandbox-*` other than
  `sandbox-proxy` (one `docker ps --filter status=running` call), plus any route in the
  activation catalog (idle-stopped instances with wake-on-request).
- Rationale: WordPress instances are `sandbox-<instance>`; generic Compose instances also
  get Caddy routes (`_generic_proxy_entries`). Counting every Sandbox project is
  conservative: it can keep the proxy for a local hosted or job stack, never stop it
  under a running instance.
- Unknown (engine error, timeout, catalog error) keeps the proxy.

## R4. Concurrency

- Decision: `fcntl.flock` on `runtime/proxy/.on-demand.lock`. Starts (`cmd_up`) hold it
  shared for the whole start; the idle stop takes it exclusive and non-blocking, and
  keeps the proxy when a start holds it.
- Rationale: the only unsafe window is a start whose containers are not yet running
  when the stop samples demand. A start that finishes before the stop samples is seen as
  a running instance; a start that begins after the stop releases sees a stopped proxy
  and starts it.

## R5. Remote-only switch

- Decision: `controller.local_runtime: docker|none` in `sandbox.local.yml`, env override
  `SANDBOX_LOCAL_RUNTIME`, registered in `MACHINE_CONFIG_PROVIDERS`.
- Alternative: reuse `domains.ingress: disabled`. Rejected: `disabled` means "no clean
  URLs", while local Docker instances still run on per-port URLs. Remote-only means no
  local container runtime at all.
- Choke points: `docker_daemon_preflight` (ensure, before any state write), `compose()`
  (every local instance Compose call), `_instance_running` (inventory, no engine call),
  `_proxy_container_running`, `_ensure_url_proxy`, `_ensure_proxy_up`.

## R6. `disabled` today

- Finding: `resolve_provider` returns `adoption=False, disabled=True` for `disabled`, and
  `clean_url_setup` / `_ensure_url_proxy` never look at `disabled`, so it still
  provisioned and started the proxy.
- Decision: `_ensure_url_proxy` and `_ensure_proxy_up` return without starting when the
  selection is disabled; the idle check treats `disabled` as no demand.

## R7. Remote-only command audit

| Path | Local Docker on the controller | Change |
| --- | --- | --- |
| CLI startup, `maybe_auto_update` | none (file writes only) | none |
| `job-start/status/output --remote` | none; `_require_local_executable` is local-only | none |
| `host apply` | only with `--local-images` (`_verify_receipt_bound_local_images`) | none: an explicit opt-in that builds on the controller by design |
| `sb host stack`, `sb remote` | none | none |
| `resources --remote` | remote adapter only | none |
| `sb web` | per request, `collect_instance_rows` -> `_instance_running` (OSError -> False) | remote-only: no engine call |
| generic Compose instances | `ComposeAdapter` runs `docker compose` through its own process runner | refused in `invoke` when remote-only |
| MCP server start | none at import or start; proxy probes at tool time only | covered by `_proxy_container_running` gate |
| `sb update` | `git pull` of the project repo | none |
| `sb doctor` | `compose ps` and proxy probes, unguarded; with no `docker` binary they raise `FileNotFoundError` | remote-only mode; missing binary reported as a check |
