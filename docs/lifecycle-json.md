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

Generic Compose and Herd responses use the same envelope and omit
`mailpit_url` when that service is not part of the runtime. For Generic Compose,
startup failures emit one bounded, redacted JSON failure document on stdout and
exit nonzero; stderr remains empty. Machine callers should branch on
`error.code`. Human output without `--json` keeps the normal error path.

Remote `ensure` applies the same typed-failure priority at the SSH boundary. A
nonzero child exit is parsed as exactly one JSON object from stdout before
stderr is considered. The child's `error.code`, `error.message`, and exit code
remain primary evidence. A harmless, redacted stderr warning may be attached as
bounded `transport.stderr`; it never replaces the typed failure. Empty,
malformed, multiple-document, and over-64-KiB stdout fail closed with
`remote_empty_output`, `remote_invalid_output`, or
`remote_output_too_large`; non-finite JSON constants are invalid too. The SSH
client concurrently drains stdout and stderr into separate 64-KiB caps and
terminates locally on overflow. Overflow and timeout report unknown completion
instead of parsing a partial document. Raw tracebacks are omitted. Human output
keeps the primary typed message, while `--json` retains the full safe envelope.
