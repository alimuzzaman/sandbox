# Implementation Plan: Clean-URL proxy on demand and remote-only controllers

**Branch**: `fix/proxy-on-demand` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/082-proxy-on-demand/spec.md`

## Summary

The `sandbox-proxy` Caddy container stops restarting itself (`restart: "no"`). `sb up`
starts a stopped, already-provisioned proxy for an instance with a clean domain again
(this call was dropped in the 2026-08-02 retirement checkpoint and never restored; the
self-restart policy hid the gap). `sb down` and `sb instance delete` run one idle check
that stops the proxy when no Sandbox container is running and no wake-on-request route
exists. A start holds a shared lease on a proxy lock; the idle stop takes it
exclusively without waiting and keeps the proxy if a start is in flight. A new
machine-level switch, `controller.local_runtime: none` (env `SANDBOX_LOCAL_RUNTIME`),
declares a remote-only controller: every local Docker entry point refuses before an
engine call, the proxy is never started, and doctor reports the mode and only runs the
checks that need no local runtime.

## Technical Context

**Language/Version**: Python 3.11+ (controller); no remote-side code changes.
**Primary Dependencies**: stdlib (`fcntl`, `subprocess`, `shutil`); existing `sandbox.core`,
`sandbox.config.manifest`, `sandbox.activation.catalog`.
**Storage**: machine-local `sandbox.local.yml` (`controller.local_runtime`); lock file
`runtime/proxy/.on-demand.lock` under `$SANDBOX_HOME`.
**Testing**: `unittest` under `tests/`, run on xspeed-dev through `sb job-start --remote`.
**Target Platform**: macOS and Linux controllers.
**Project Type**: CLI.
**Performance Goals**: the idle check adds one `docker ps` (5 s bound) to `down`/`delete`.
**Constraints**: never stop the proxy while any Sandbox container runs; unknown is not
idle; no root, DNS, CA or sudoers change; constitution VI (the default provider stays
the default).
**Scale/Scope**: ten touched modules, two new pure modules.

## Constitution Check

- **I. Per-project instances**: unchanged.
- **II. Registry is the source of truth**: demand reads the registry through
  `registry_all()` (activation catalog) and the engine through `docker ps`; no JSON read.
- **III. Single entry file, modular package**: new pure modules under `sandbox/config/`
  and `sandbox/application/`; the machine switch registers in
  `MACHINE_CONFIG_PROVIDERS`.
- **IV. Live-stack verification**: unit tests on xspeed-dev; the live Mac proxy is not
  touched (owner's rule). Live verification of the on-demand stop is left to the
  orchestrator and recorded as such.
- **V. Idempotency and docs-with-code**: the idle stop is a no-op when already stopped;
  docs land in the same change.
- **VI. Parity before removal**: the default provider is unchanged and still started by
  lifecycle. `remote-only` and `disabled` are explicit opt-outs, not a stub; the guard
  test on `_ensure_url_proxy` keeps passing. Restoring the `up` start closes a parity gap
  the new restart policy would otherwise open.

Gate: PASS. Post-design re-check: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/082-proxy-on-demand/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/cli.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/config/local_runtime.py          # new: pure switch normalization + resolution
sandbox/config/manifest.py               # register controller.local_runtime
sandbox/application/proxy_demand.py      # new: pure stop/keep decision
sandbox/core/_domains.py                 # restart policy, lease/lock, stop_proxy_if_idle,
                                         # gates in _ensure_url_proxy/_ensure_proxy_up/
                                         # _proxy_container_running/proxy_down, mode facade
sandbox/core/_docker.py                  # compose()/docker_daemon_preflight refusal
sandbox/core/_instances.py               # _instance_running: no engine call when remote-only
sandbox/runtimes/compose.py              # generic Compose adapter refuses when remote-only
mcp/wp-server/app.py                     # MCP proxy probe goes through the gated core probe
sandbox/commands/lifecycle.py            # cmd_up start + lease, cmd_down idle stop, doctor
sandbox/commands/instances_cmd.py        # idle stop after instance delete
sandbox/cli.py                           # doctor needs no instance when remote-only
tests/test_local_runtime.py              # new
tests/test_proxy_on_demand.py            # new
tests/test_setup_idempotency.py          # cmd_up proxy contract updated
tests/test_instance_ready_install_state.py # cmd_down test patches the idle check
docs/clean-url-default.md, docs/host-ingress.md, README.md, CLAUDE.md
```

**Structure Decision**: existing single-package layout; no file in the parallel-work
exclusion list (`ui_dash.py`, `hosting/panel_*`, `src/web`, `host_stack.py`, hosting job
paths, `sandbox/resources/*`, `tailnet_names`, `login_service`, `test_lifecycle.py`,
`test_remote_hosting_images.py`, `test_server_transport.py`) is touched.

## Complexity Tracking

None.
