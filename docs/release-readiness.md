# Release Readiness Checklist

Use this checklist before calling a Sandbox release ready. A checked item must
link to dated live evidence; a green unit suite is not a substitute for a live
acceptance gate.

## Completed in this workspace

- [x] Public HTTPS remote MCP acceptance: a second MCP client reached
  `https://sandbox-control.asb.bd/mcp` and ran file read, browser visit, WP-CLI,
  and PHPUnit against a VPS-side project.
- [x] Plugin Check baseline acceptance: `alims-builder-authoring` established
  its reviewed 17-error baseline and the immediate non-updating rerun reported
  zero new errors.
- [x] Generic Compose MCP acceptance: lifecycle status, logs, and argv-based
  container execution completed through the active MCP server.

## Required release gates

- [ ] Run `./sb doctor` for every supported local runtime. Its Remote targets
  section must show SSH, provisioning, transport configuration, and MCP-route
  checks passing for every remote included in the release.
- [ ] Restart the MCP client after any MCP tool registration or transport change,
  then call the changed tool through that client.
- [ ] On a Herd machine, repeat WordPress abilities, background-job, and
  test-runner parity scenarios; Docker-only evidence does not prove Herd.
  Current host has Herd 1.29.0 but its MySQL endpoint is unavailable; operator
  action is required before these scenarios can run.
- [x] Relocated to a disposable runtime base and booted a project from it
  (spec 009; verified 2026-07-16).
- [ ] Exercise dashboard DB-only snapshot and reset controls when that UI slice
  is implemented (spec 008).
- [ ] Run focused suites, the full Sandbox suite, `./sb selftest`, and
  `git diff --check` against the candidate revision.

## Protected gates

- [ ] Real encrypted Drive backup, verification, and fresh-server restore drill
  (specs 018 and 023): requires configured credentials and explicit operator
  approval for external storage operations.
- [ ] Hermes public-dashboard/gateway acceptance and Lenzora deployment (specs
  022, 026, and 027): requires the named remote environment and explicit
  operator authorization; no local substitute is accepted.
- [ ] Legacy Drive deletion or recovery-schedule activation: requires the exact
  reviewed plan plus separate deletion or scheduling authorization.

## MCP subprocess wrapper contract

Every thin MCP wrapper that shells out to `sb --json` must use
`app._run_sandbox_json` and test its documented timeout, final-JSON selection,
and parse-failure response. Each wrapper also tests its own redaction of SSH
targets, tokens, and other secrets before returning an error.
