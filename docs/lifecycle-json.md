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
`mailpit_url` when that service is not part of the runtime. Startup failures
still use the normal nonzero CLI error path; callers must treat a missing
success document as unsuccessful and inspect the error stream.
