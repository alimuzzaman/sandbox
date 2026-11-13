# Lifecycle JSON output

`sb up --json` starts the selected local instance and emits one JSON success
document after the start operation completes. The flag is additive; without it,
the existing human-readable output is unchanged.

Example:

```sh
./sb --instance demo up --json
```

WordPress output includes the instance, runtime kind, site URL, and Mailpit
URL:

```json
{
  "command": "up",
  "instance": "demo",
  "mailpit_url": "http://localhost:8025",
  "ok": true,
  "runtime": "wordpress",
  "url": "http://demo.tst"
}
```

For WordPress instances with `phpExtensions`, `up` prepares missing
Sandbox-managed child images before Compose startup. A preparation failure
emits one bounded, redacted JSON failure document with
`error.code: "php_extension_image_prepare_failed"`; Compose is not started.
Successfully built local images are retained for a later retry.

Generic Compose and Herd responses use the same envelope and omit
`mailpit_url` when that service is not part of the runtime. For Generic Compose,
startup failures emit one bounded, redacted JSON failure document on stdout and
exit nonzero; stderr remains empty. Machine callers should branch on
`error.code`. Human output without `--json` keeps the normal error path.

`sb apply --json` emits its redacted project result as one JSON document on
stdout. When the CLI infers the project from `--instance` or the current
directory, its explanatory routing message is omitted in JSON mode; human mode
continues to show that message.

Remote `ensure` applies the same typed-failure priority at the SSH boundary. A
nonzero child exit is parsed as exactly one JSON object from stdout before
stderr is considered. The child's `error.code`, `error.message`, and exit code
remain primary evidence. A harmless, redacted stderr warning may be attached as
bounded `transport.stderr`; it never replaces the typed failure. Empty,
malformed, multiple-document, and over-64-KiB stdout fail closed with
`remote_empty_output`, `remote_invalid_output`, or
`remote_output_too_large`; non-finite JSON constants are invalid too. Exit 255
SSH failures report `remote_authentication_failed`, `remote_unreachable`, or
`remote_connection_lost`. These name the likely cause from SSH's message, which
cannot prove when the failure happened and may come from the remote command itself,
and deployment runs before it. Treat completion as unknown and inspect before
retrying. The SSH
client concurrently drains stdout and stderr into separate 64-KiB caps and
terminates locally on overflow. Overflow and timeout report unknown completion
instead of parsing a partial document. Raw tracebacks are omitted. Human output
keeps the primary typed message, while `--json` retains the full safe envelope.

### Conditional instance deletion

`sb instance delete NAME --local --yes --creation-context-json JSON
--expected-incarnation inc_…` is the preview teardown port. Both conditional
options are mandatory together. The receiver checks the original creation-request
ledger and receipt against the current canonical project/label/name/incarnation,
requires a succeeded `created` relation, and refuses mismatch before teardown.
It does not mint new receipts or accept a name/label as proof. Installed parsers
without these options fail closed; the caller never retries without them.

The host-wide instance lifecycle guard orders before project and registry locks.
It covers the full WordPress ensure operation, native/generic runtime
ensure/apply/destroy/start/stop/resume/suspend/ownership-repair, explicit CLI
deletion, project-lock callers, and canonical
registry put/remove (including the MCP recreate pending-record transition).
Nested calls retain one reentrant process/thread guard. Independent lifecycle
writers on this host consequently serialize. Inventory keeps its existing read
path. CLI deletion bypasses automatic migration and generated-runtime rewrites;
configuration is read inside the guard and owner comparison precedes effects.
Preview teardown separately retains unknown outcomes before dispatch, and does
not authorize another attempt from a timeout or process-disappearance observation.
No concurrency/runtime checks were run for this source-only change.

Generic Compose status/logs/exec do not persist their captured owner record.
Successful logs/exec therefore cannot resurrect an old incarnation/receipt after
a concurrent replacement. Runtime-changing start/stop/resume/suspend and ownership
repair join ensure/apply/destroy under the full shared writer guard before the
record read, through effects and any final registry update. Long-running ordinary
logs/exec do not hold the host lifecycle guard merely to capture output.
