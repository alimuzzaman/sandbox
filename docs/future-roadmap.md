# Sandbox Future Roadmap

Reviewed: 2026-07-09

This roadmap is based on the current worktree, specs, and docs. It is not a wish list:
items are included because an existing spec/doc/test surface names the gap, or because a
current implementation is intentionally marked as pending live proof.

## Current baseline

Sandbox has already crossed the main architectural line: a single MCP server routes by
`project_dir`; projects carry their own `sandbox.config.*`; the registry is per root;
the live WordPress tool surface includes PHPUnit, Plugin Check, editor-schema catalog
fallbacks, debugging tools, snapshots/reset, CI/e2e fan-out, and a first remote VPS path.

Green local evidence from the latest review pass:

- `.cli-venv/bin/python -m unittest -q tests/test_plugin_check.py` passes.
- Full suite: `/Users/alim/Sites/git/sandbox/.cli-venv/bin/python -m unittest discover -s tests`
  passed from this worktree: 250 tests, 2 skipped.

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

2. **Plugin Check post-fix live re-run**

   Evidence: `docs/plugin-check.md` records two live-found bugs now fixed in unit tests:
   absolute path to relative baseline keys, and `.distignore` auto-excludes. It explicitly
   says both fixes together still need a real repo re-run.

   Next work: re-run `run_plugin_check` or `./sb plugin-check` against the original real
   plugin repo and confirm the noise drops to the expected baseline-sized signal.

3. **Editor-schema remaining proof**

   Evidence: `specs/011-eb-attribute-schema/tasks.md` leaves EB Pro full-fidelity proof
   and final evidence assembly qualified. `specs/012-bundled-schema-catalog/tasks.md`
   leaves optional sampled validation unbuilt.

   Next work: map a full EB Pro source checkout and prove a Pro block resolves at full
   fidelity; then decide whether `schema-catalog validate --sample <n>` should move from
   optional to release-gate for catalog refreshes.

## P1 - Reliability And Release Readiness

1. **Turn deferred live checks into a single release checklist**

   Evidence: pending or partial verification is spread across specs 003, 004, 008, 009,
   011, 012, 013, and 014. This makes "are we ready?" harder to answer than it should be.

   Next work: create one release-gate document or command that reports all deferred
   validations: Herd parity, runtime relocation, dashboard reset/snapshot UI, Plugin
   Check live run, remote VPS live run, and schema-catalog drift.

2. **Doctor should know more about modern features**

   Evidence: roadmap/readiness docs already treat `doctor` as the health surface, while
   new features added hidden dependencies: MCP venv freshness, Plugin Check installability,
   schema catalog presence/version drift, HTTPS/Tailscale control reachability, and
   remote config shape.

   Next work: extend `./sb doctor` with feature probes and actionable messages.

3. **MCP wrapper contract parity**

   Evidence: `remote_deploy` and `run_plugin_check` both needed review fixes so failure
   responses matched their documented JSON contracts.

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

## P3 - Authoring And Design Automation

1. **Stateful Gutenberg/Elementor finalization**

   Evidence: spec 011 found that correct schema alone does not guarantee non-empty static
   block rendering; static blocks need finalizer/editor save behavior.

   Next work: re-home the render-proof work to spec 005/editor authoring and make
   `gutenberg-finalize` / Elementor stateful authoring the canonical path for blocks that
   cannot be represented by attributes alone.

2. **Figma-to-WordPress pipeline**

   Evidence: `docs/vision.md` names Figma/screenshot-to-Gutenberg/Elementor as a core
   product goal; README still lists Figma MCP as a future item.

   Next work: start with a narrow spec: import a Figma frame, map it to known block/widget
   primitives using editor-schema, render in a sandbox page, and verify via screenshot diff.

## P4 - Dashboard And Product Polish

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
