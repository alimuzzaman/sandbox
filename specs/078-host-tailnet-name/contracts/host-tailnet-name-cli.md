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
    {"step": "activate", "status": "done", "detail": "tested and reloaded"},
    {"step": "verify_tailnet", "status": "done", "detail": "HTTP 200"},
    {"step": "verify_public", "status": "done", "detail": "public address refused (refused)"}
  ],
  "record": {"upstream": "127.0.0.1:8025", "tailnet_ip": "100.64.0.10",
             "cert_not_after": "2027-02-08T00:00:00Z"}
}
```

On refusal or failure: `"ok": false`, plus top-level `"code"` and `"error"`
(secret-free), and `steps` up to and including the one that stopped.

Steps, in order: `add` runs `preflight`, `gate`, `dns`, `certificate`,
`activate`, `verify_tailnet`, `verify_public`; `renew` runs `certificate`
(skipped with `tailnet_name_not_due` when not due and the activation is
proven loaded and verified) or, when it has work to do, `gate`,
`certificate`, `activate`, `verify_tailnet`, `verify_public`; `remove` runs
`preflight`, `gate`, `remove_block`, `remove_certificate`, `remove_dns`.
`activate` installs the block and chain together under the edge lock with an
activation journal (hardening, 2026-11-10).

`list` returns `{"ok", "operation": "list", "remote", "names": [...]}` where
each name has `fqdn`, `upstream`, `cert_not_after`, `days_remaining`,
`renewal_due`, `block_present`, `cert_present`, `recorded`, `drift` (a list of
strings: `block_missing`, `not_recorded`, `unmanaged_block`,
`unmanaged_directory`, `certificate_missing`, `activation_<state>` or
`activation_unproven`, `upstream_differs`, `tailnet_address_differs`,
`zone_not_allowed`, `state_<state>`).

`renew --all` returns `{"ok", "operation": "renew", "remote", "results": [...]}`
with one per-name envelope (without `remote`) for each name, and `ok` false if
any result is not ok.

## Refusal and failure codes

| Code | When |
|---|---|
| `tailnet_name_usage` | Missing or misplaced arguments for the action (exit 1, before any other check). |
| `tailnet_name_public_address_invalid` | `--public-address` is not a public IPv4 address. |
| `tailnet_name_remote_failed` | The remote program could not run, returned no result, or failed unexpectedly (for example a write error); any interrupted activation is settled by the next run. |
| `tailnet_name_invalid` | FQDN fails hostname rules, is a wildcard or a zone apex. |
| `tailnet_name_zone_not_allowed` | FQDN is under no configured zone, or its stored record names another zone; checked before any DNS or ACME call or remote change. |
| `tailnet_name_upstream_invalid` | Upstream is not `IPV4:PORT` or `localhost:PORT`, or not loopback/private/tailnet. |
| `tailnet_name_config_missing` / `tailnet_name_config_invalid` | `tailnet_names` block absent or malformed. |
| `tailnet_name_remote_unknown` | `--remote` is not registered. |
| `tailnet_name_no_tailnet_address` | The registration has no tailnet IPv4. |
| `tailnet_name_tailnet_address_unbound` | The remote does not hold that address. |
| `front_door_capability_unavailable` | The remote's front door is not nginx. |
| `front_door_*` | nginx preflight refusals (missing, inactive, invalid, conflict). `add` first settles an unfinished activation of the same name and checks again. |
| `tailnet_name_acme_client_missing` | `lego` is not found. |
| `tailnet_name_acme_client_untrusted` | `lego` is not pinned: not a root/user-owned, non-writable file in a known install path or matching `lego_sha256`, or its `--version` probe did not report lego 5+. |
| `tailnet_name_unmanaged` | A block or certificate directory at the name's reserved paths was not written by Sandbox (no header for this name, not root 0600, no `managed.json`, or unknown files); it is left alone. |
| `tailnet_name_remote_path_unsafe` | A remote path is a symlink, not a regular file/directory, or has an untrusted owner or mode (including a private key that is not root 0600). |
| `tailnet_name_state_corrupt` | The controller state file does not parse; nothing is changed. |
| `tailnet_name_public_probe_inconclusive` | The public check timed out, could not route, failed DNS or errored, or presented an undecodable certificate. |
| `tailnet_name_busy` | A durable job is active on the remote or a host transaction lock is held (checked again inside the edge lock, before `nginx -t` and before the reload), this controller is already working on the remote, or another run holds the name's remote lease (or this run lost it, checked again before every DNS or ACME call). |
| `tailnet_name_gate_unavailable` | The job query could not answer, or the remote Sandbox home or job registry could not be verified (fails closed). A lock that cannot be probed counts as held and is reported as `tailnet_name_busy`. |
| `tailnet_name_secret_unavailable` | The broker refused or could not provide the token. |
| `tailnet_name_dns_conflict` | The name's complete record set is not empty and not exactly one identical DNS-only A record. |
| `tailnet_name_dns_failed` | The DNS provider call failed. |
| `tailnet_name_dns_not_owned` | `remove`: the name's complete record set is not exactly the recorded record (id, type A, address, no proxy). |
| `tailnet_name_upstream_differs` | `add`: published with another upstream and no `--replace-upstream`. |
| `tailnet_name_certificate_failed` | `lego` failed, the remote could not make the key or signing request, or the chain does not match the key or the name (the remote refused it before installing anything). |
| `tailnet_name_install_failed` | The remote has no private key for the name to install a chain against; rerun `add`. |
| `tailnet_name_nginx_rejected` | `nginx -t` failed; the previous block and chain were restored, nginx not reloaded (on `remove`, a block already off disk stays recorded as an unproven removal). |
| `tailnet_name_reload_failed` | The reload failed or nginx did not start serving the new configuration (the name's activation marker and certificate were not observed), or an interrupted activation could not be settled; previous state restored where possible. |
| `tailnet_name_tailnet_probe_failed` | The tailnet HTTPS check failed. |
| `tailnet_name_public_exposed` | The public address presented a certificate valid for the name (ours or another); the block was removed and the detail says whether the name is still served. |
| `tailnet_name_public_probe_unavailable` | No public address is known for the check. |
| `tailnet_name_not_found` | `renew`/`remove`: no such name on the remote or in the state. |
| `tailnet_name_not_due` | `renew` without `--force` on a certificate with more than 30 days left whose exact loaded bytes are proven by the remote and passed both probes (status `skipped`, not an error; `ok` stays true). |

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
