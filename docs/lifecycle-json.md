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
