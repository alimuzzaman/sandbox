# Quickstart: Generic Local and Remote Deployment

1. Declare a generic project with `kind: "compose"`, a project-relative Compose file,
   public service, internal port, and health path. See
   [project-config contract](contracts/remote-deploy.md).
2. Run `./sb ensure --project-dir /path/to/project --json`; verify `kind: "compose"`,
   `status: "ready"`, `http_port`, and `url`.
3. Register and provision the remote once: `./sb remote add NAME SSH_TARGET`, then
   `./sb remote provision NAME --control-host HOST`.
4. Deploy and expose: `./sb deploy --project-dir /path/to/project --remote NAME
   --ensure --expose --domain app.example.com --json`.
5. Verify the returned URL reaches the declared health path. Repeat the deploy after a
   source edit and confirm only current committed/uncommitted state is reflected.

Failure checks: an absent service/port/health declaration fails before remote contact;
a failing remote health check returns `ok: false`; an explicit `--plugin-slug` on a
generic project is rejected as WordPress-only.

## Observed local evidence (2026-07-18)

`./sb ensure --project-dir tests/fixtures/generic-compose --json` returned a ready
`kind: "compose"` instance twice on the same `http_port`, with its declared `web`
service and `health_path: "/"`. Focused remote/runtime tests passed before the live
fixture check. Remote deployment requires a user-supplied registered remote and was
not run against an external host in this change.

The scoped MCP server imports also returned exactly `instances,runtime,net,remote`
for the generic fixture and `instances,wp,net,data,fs,mail,context,remote` for the
Sandbox WordPress project. The complete Python suite passed: 844 tests, 1 skipped.
