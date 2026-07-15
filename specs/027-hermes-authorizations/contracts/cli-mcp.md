# CLI and MCP Contract

## CLI

```text
sb hermes update provenance --version TAG [--commit COMMIT] --remote NAME
sb hermes authorization list --remote NAME
sb hermes authorization sync --remote NAME
sb hermes authorization show REQUEST_ID --remote NAME
sb hermes authorization request --job JOB --scope SCOPE --replay-origin ORIGIN --reason TEXT [--expires-in-minutes N] --remote NAME
sb hermes authorization approve REQUEST_ID --confirm --remote NAME
```

`update provenance` is read-only and verifies a signed immutable release in a disposable
checkout without changing the installed Hermes checkout. `update apply` remains separately
confirmation-gated.

`list` and `show` are read-only. `request` creates a pending record. `approve` performs the protected lifecycle transition and updates the matching catalog job prompt.
`sync` converts non-secret terminal `REVIEW_REQUIRED` cron output into review-only drafts; it never approves or runs work.

## MCP

```text
hermes_authorization_list(remote)
hermes_authorization_sync(remote)
hermes_authorization_show(remote, request_id)
hermes_authorization_request(remote, job, scope, replay_origin, reason, expires_in_minutes=1440)
hermes_authorization_approve(remote, request_id, confirm=false)
```

All responses use the existing Hermes result envelope. Approval requires `confirm=true`.
