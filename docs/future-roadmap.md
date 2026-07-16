# Sandbox Future Roadmap

Reviewed: 2026-07-16

This roadmap is based on the current worktree, specs, and docs. It is not a wish list:
items are included because an existing spec/doc/test surface names the gap, or because a
current implementation is intentionally marked as pending live proof.

## Current baseline

Sandbox has already crossed the main architectural line: a single MCP server routes by
`project_dir`; projects carry their own `sandbox.config.*`; the registry is per root;
the live WordPress tool surface includes PHPUnit, Plugin Check, debugging tools,
snapshots/reset, CI/e2e fan-out, and a first remote VPS path.

Green local evidence from the latest review pass:

- `.cli-venv/bin/python -m unittest -q tests/test_plugin_check.py` passes.
- Full suite: `/Users/alim/Sites/git/sandbox/.cli-venv/bin/python -m unittest discover -s tests`
  passed from this worktree: 835 tests, 1 skipped.
- Capability-gated MCP tools now resolve the repository package path before importing
  shared runtime services; the MCP smoke test covers launches from the server directory.
- `./sb doctor` now probes that same MCP import boundary, rather than checking only for
  the venv file, and reports a restart/install hint when it is unhealthy.
- When a project declares Plugin Check, `doctor` now verifies that the tool is installed
  and active before Plugin Check calls are attempted.

Live external evidence still matters. Several specs explicitly say unit tests are not
enough for "done."

## P0 - Proof Gates Before Calling Recent Work Done

1. **Remote VPS live validation**

   Evidence: `specs/014-remote-vps-hosting/tasks.md` leaves T044 pending; `docs/remote-hosting.md`
   says the local/unit surface is implemented but the real `remote provision` -> `deploy`
   -> remote MCP instance pipeline is still unverified. A live run against a fresh
   Ubuntu 24.04 VPS (`alim@212.47.72.49`) proved package/runtime bootstrap through
   Docker, Caddy, MCP venv, and Playwright tooling after installer fixes. That run
   exposed the old Tailscale-only assumption; the implementation now defaults to a
   public HTTPS control endpoint and keeps Tailscale as opt-in. The VPS currently serves
   the control endpoint at `https://sandbox-control.asb.bd`, with the MCP process bound
   to loopback behind Caddy.

   Next work: run `specs/014-remote-vps-hosting/quickstart.md` Phase 0 plus scenarios 1-5
   through a registered second MCP server. Capture `fs_read`, `visit`, `wp_cli`, and
   `run_tests` evidence from that HTTPS endpoint.

2. **Plugin Check baseline acceptance**

   Evidence: `docs/plugin-check.md` records the live 2026-07-16 run against
   `alims-builder-authoring`: relative path identity is now correct, and the gate
   reported 17 errors and 8 warnings without `../sandbox` leakage. The project has no
   baseline, so this proves parser/scan behavior but not a baseline-gate pass.

   Next work: an owner-approved `--update` run on a repository with reviewed findings,
   followed by a non-updating pass, is still required to prove baseline acceptance.

## P1 - Reliability And Release Readiness

1. **Turn deferred live checks into a single release checklist**

   Evidence: pending or partial verification is spread across specs 003, 004, 008, 009,
   013, and 014. This makes "are we ready?" harder to answer than it should be.

   Next work: create one release-gate document or command that reports all deferred
   validations: Herd parity, runtime relocation, dashboard reset/snapshot UI, Plugin
   Check live run, and remote VPS live run.

2. **Doctor should know more about modern features**

   Evidence: roadmap/readiness docs already treat `doctor` as the health surface, while
   new features added hidden dependencies: MCP venv freshness, HTTPS/Tailscale control reachability, and
   remote config shape.

   Next work: extend `./sb doctor` with remaining remote and release-readiness probes.

3. **MCP wrapper contract parity**

   Evidence: `remote_deploy` and `run_plugin_check` both needed review fixes so failure
   responses matched their documented JSON contracts. They now share the bounded
   `_run_sandbox_json` subprocess boundary for last-JSON parsing and timeout results,
   with wrapper-specific response shapes and redaction retained.

   Next work: add a shared helper/test pattern for thin MCP subprocess wrappers so new
   wrappers cannot drift on timeout/parse-failure shapes.

## P2 - Remote Hosting V2

1. **Provisioning hardening**

   Evidence: `scripts/install-remote.sh` is documented as installing Docker via reused
   package-manager logic, but the current script is still mostly Ubuntu/apt-shaped.
   Fresh VPS validation also showed why the provisioner must upload the local sandbox
   runtime instead of assuming anonymous GitHub clone access.

   Next work: either narrow the docs/spec to Ubuntu-only for v1, or implement dnf/pacman/
   zypper branches equivalent to local setup support.

2. **Remote lifecycle UX**

   Evidence: remote hosting currently requires manual second-MCP registration and a
   one-time bearer token shown during provision. Fresh VPS validation also requires
   either passwordless sudo for the SSH user or a root/bootstrap step, plus a DNS/control
   hostname for the default HTTPS path or a Tailscale auth key/manual `tailscale up` for
   the optional private path.

   Next work: add a safer "show registration command once / rotate token / verify
   remote MCP health" flow without exposing stored tokens in normal list/status output.

3. **Shared VPS port policy**

   Evidence: this VPS is intended to host other apps too, such as Next.js. Remote MCP now
   uses a Caddy-routed HTTPS hostname by default, but booted WordPress instances still
   publish Docker ports on the VPS host. They use high ports, not `3000`, but the
   bind/exposure policy should be explicit before this becomes a shared-app host.

   Next work: add a remote-mode port policy: default instance ports bind to localhost or
   an explicit interface only, document how they coexist with Caddy/Nginx/Next.js, and
   keep public WordPress site routing opt-in.

4. **Remote API surface**

   Evidence: the README previously named phone/Slack/FluentBoards triggering as "Next";
   remote VPS hosting covers runtime placement, not event-triggered remote operations.

   Next work: spec an authenticated automation surface for triggering safe sandbox jobs
   from Slack/FluentBoards/mobile, with explicit allowlisted actions and audit logs.

## P3 - Dashboard And Product Polish

1. **Dashboard parity for snapshots/reset**

   Evidence: `specs/008-db-snapshots-reset/tasks.md` defers dashboard DB-only snapshot and
   reset controls.

   Next work: finish bridge routes and UI controls so wp-admin/dashboard users can do the
   same reset/snapshot operations already available by CLI/MCP.

2. **Telemetry and hot reload**

   Evidence: `docs/v1-checklist.md` leaves opt-in telemetry and MCP hot-reload deferred.

   Next work: keep telemetry product-led and opt-in. For hot reload, prototype a wrapper
   only if MCP clients expose a dependable reconnect story; otherwise keep documenting the
   restart requirement after server tool changes.
