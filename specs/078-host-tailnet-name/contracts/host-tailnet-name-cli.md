# Contract: `sb host tailnet-name`

```text
sb host tailnet-name add    FQDN --remote R --upstream HOST:PORT
                            [--replace-upstream] [--public-address IPV4]
                            [--acme-server letsencrypt|letsencrypt-staging|URL] [--json]
sb host tailnet-name list   --remote R [--json]
sb host tailnet-name renew  (FQDN | --all) --remote R [--force]
                            [--public-address IPV4] [--acme-server ...] [--json]
sb host tailnet-name remove FQDN --remote R [--json]
```

`FQDN` is the third positional of `sb host`. It is refused for every other
host action.

## Exit status

`0` when `ok` is true, otherwise `1`. Argument errors exit `2` (argparse).

## `--json` envelope

One JSON object on stdout:

```json
{
  "ok": true,
  "operation": "add",
  "remote": "R",
  "fqdn": "app.example.test",
  "steps": [
    {"step": "preflight", "status": "done"},
    {"step": "gate", "status": "done"},
    {"step": "dns", "status": "skipped", "detail": "identical record exists"},
    {"step": "certificate", "status": "done"},
    {"step": "install", "status": "done"},
    {"step": "server_block", "status": "done"},
    {"step": "verify_tailnet", "status": "done", "detail": "HTTP 200"},
    {"step": "verify_public", "status": "done", "detail": "handshake refused"}
  ],
  "record": {"upstream": "127.0.0.1:8025", "tailnet_ip": "100.64.0.10",
             "cert_not_after": "2027-02-08T00:00:00Z"}
}
```

On refusal or failure: `"ok": false`, plus top-level `"code"` and `"error"`
(secret-free), and `steps` up to and including the one that stopped.

`list` returns `{"ok", "operation": "list", "remote", "names": [...]}` where
each name has `fqdn`, `upstream`, `cert_not_after`, `days_remaining`,
`renewal_due`, `block_present`, `cert_present`, `recorded`, `drift` (a list of
strings).

`renew --all` returns `{"ok", "operation": "renew", "remote", "results": [...]}`
with one per-name envelope (without `remote`) for each name, and `ok` false if
any result is not ok.

## Refusal and failure codes

| Code | When |
|---|---|
| `tailnet_name_invalid` | FQDN fails hostname rules, is a wildcard or a zone apex. |
| `tailnet_name_zone_not_allowed` | FQDN is under no configured zone. |
| `tailnet_name_upstream_invalid` | Upstream is not `IPV4:PORT` or `localhost:PORT`, or not loopback/private/tailnet. |
| `tailnet_name_config_missing` / `tailnet_name_config_invalid` | `tailnet_names` block absent or malformed. |
| `tailnet_name_remote_unknown` | `--remote` is not registered. |
| `tailnet_name_no_tailnet_address` | The registration has no tailnet IPv4. |
| `tailnet_name_tailnet_address_unbound` | The remote does not hold that address. |
| `front_door_capability_unavailable` | The remote's front door is not nginx. |
| `front_door_*` | nginx preflight refusals (missing, inactive, invalid, conflict). |
| `tailnet_name_acme_client_missing` | `lego` is not found. |
| `tailnet_name_busy` | A durable job is active on the remote, a host transaction lock is held, or this controller is already working on the remote. |
| `tailnet_name_gate_unavailable` | The job query or lock probe could not answer. |
| `tailnet_name_secret_unavailable` | The broker refused or could not provide the token. |
| `tailnet_name_dns_conflict` | A record with another address, a proxied record or several records exist. |
| `tailnet_name_dns_failed` | The DNS provider call failed. |
| `tailnet_name_dns_not_owned` | `remove`: the record no longer matches the recorded id, address and no-proxy. |
| `tailnet_name_upstream_differs` | `add`: published with another upstream and no `--replace-upstream`. |
| `tailnet_name_certificate_failed` | `lego` failed, or the issued chain does not match the key or the name. |
| `tailnet_name_install_failed` | Writing the certificate or the renewal transaction failed (previous chain restored). |
| `tailnet_name_nginx_rejected` | `nginx -t` failed; the new file was removed, nginx not reloaded. |
| `tailnet_name_reload_failed` | The reload failed; previous state restored. |
| `tailnet_name_tailnet_probe_failed` | The tailnet HTTPS check failed. |
| `tailnet_name_public_exposed` | The public address presented our certificate; the block was removed. |
| `tailnet_name_public_probe_unavailable` | No public address is known for the check. |
| `tailnet_name_not_found` | `renew`/`remove`: no such name on the remote or in the state. |
| `tailnet_name_not_due` | `renew` without `--force` on a certificate with more than 30 days left (status `skipped`, not an error; `ok` stays true). |

## Secrets

The token never appears in argv (of `sb` or any child), the environment of any
process except the broker's child, the state file, `sandbox.local.yml`, stdout,
stderr, `--json`, logs, the audit trail or feedback. `sandbox.local.yml` holds
only the broker source and key names.

## Host-header checks in proxied apps

nginx passes `Host: <fqdn>`. An app that validates the Host header (the
Sandbox panel's DNS-rebinding guard accepts only `*.ts.net` today) must allow
the name itself; for the panel that is `sb web --allow-host <fqdn>` once the
panel's allow-list ships.
