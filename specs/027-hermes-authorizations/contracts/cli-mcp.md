# CLI and MCP Contract

## CLI

```text
sb hermes authorization list --remote NAME
sb hermes authorization show REQUEST_ID --remote NAME
sb hermes authorization request --job JOB --scope SCOPE --replay-origin ORIGIN --reason TEXT [--expires-in-minutes N] --remote NAME
sb hermes authorization approve REQUEST_ID --confirm --remote NAME
```

`list` and `show` are read-only. `request` creates a pending record. `approve` performs the protected lifecycle transition and updates the matching catalog job prompt.

## MCP

```text
hermes_authorization_list(remote)
hermes_authorization_show(remote, request_id)
hermes_authorization_request(remote, job, scope, replay_origin, reason, expires_in_minutes=1440)
hermes_authorization_approve(remote, request_id, confirm=false)
```

All responses use the existing Hermes result envelope. Approval requires `confirm=true`.
