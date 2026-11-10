# Data Model: Tailnet-Only Hostnames

Types live in `sandbox/hosting/tailnet_names/models.py`. All are frozen
dataclasses parsed at the boundary (CLI arguments, `sandbox.local.yml`, the
state file, remote program output, child output); nothing downstream handles
raw dicts.

## TailnetNameConfig

Parsed from `tailnet_names` in `sandbox.local.yml`.

| Field | Type | Rule |
|---|---|---|
| `zones` | `tuple[str, ...]` | Required, non-empty; each a lowercase IDNA-clean hostname with at least two labels; duplicates removed. |
| `token_source` | `str` | Required; a broker source name (validated by the broker). |
| `token_key` | `str` | Required; a broker key name (validated by the broker). |
| `acme_email` | `str` | Required; one `@`, no whitespace, at most 254 characters. |
| `acme_server` | `str` | `letsencrypt` (default), `letsencrypt-staging`, or an `https://` URL. |
| `lego_path` | `str \| None` | Absolute path; default is `lego` resolved on `PATH` at run time. |

Missing or invalid block: `tailnet_name_config_missing` /
`tailnet_name_config_invalid`.

## TailnetName (validated request)

| Field | Type | Rule |
|---|---|---|
| `fqdn` | `str` | Lowercase, IDNA-clean, labels `[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?`, total length up to 253, no `*`, not equal to a zone. |
| `zone` | `str` | The longest configured zone of which `fqdn` is a proper subdomain; else `tailnet_name_zone_not_allowed`. |
| `route_name` | `str` | `tailnet-<fqdn>`; file `/etc/nginx/conf.d/sandbox-tailnet-<fqdn>.conf`. |
| `cert_dir` | `str` | `/etc/nginx/sandbox-tailnet/<fqdn>`. |

## Upstream

| Field | Type | Rule |
|---|---|---|
| `host` | `str` | IPv4 literal; `localhost` becomes `127.0.0.1`. Must be loopback, RFC 1918 private or in `100.64.0.0/10`. |
| `port` | `int` | 1-65535. |

Invalid: `tailnet_name_upstream_invalid`.

## RemoteTarget

| Field | Type | Source |
|---|---|---|
| `name` | `str` | `--remote`. |
| `entry` | `dict` | `_remote.get_remote(name)`; unknown means `tailnet_name_remote_unknown`. |
| `tailnet_ip` | `IPv4Address` | `entry["tailscale_host"]`, must be in `100.64.0.0/10`; else `tailnet_name_no_tailnet_address`. |
| `public_ip` | `IPv4Address \| None` | `--public-address`, else `remote_ssh_parts(entry)["host"]` resolved and `is_global`. |

The front door must grant `TAILNET_NAMES` (nginx only), else
`front_door_capability_unavailable`.

## StepOutcome

| Field | Type |
|---|---|
| `step` | `Literal["preflight","gate","dns","certificate","install","server_block","verify_tailnet","verify_public","remove_block","remove_certificate","remove_dns"]` |
| `status` | `Literal["done","skipped","refused","failed","not_run"]` |
| `code` | `str \| None` (stable refusal or failure code) |
| `detail` | `str \| None` (bounded, secret-free) |

## TailnetNameRecord (controller state)

File: `$SANDBOX_HOME/runtime/tailnet-names/<remote>.json`, mode 0600,
`{"version": 1, "names": {"<fqdn>": {...}}}`.

| Field | Type | Meaning |
|---|---|---|
| `fqdn`, `zone`, `upstream`, `tailnet_ip` | `str` | As validated. |
| `dns_record_id` | `str \| None` | Cloudflare id created or adopted by the DNS step. |
| `dns_adopted` | `bool` | The record existed and matched; it was not created here. |
| `cert_not_after` | `str \| None` | ISO-8601 UTC expiry of the installed leaf. |
| `cert_sha256` | `str \| None` | Hex SHA-256 of the installed leaf (DER). |
| `steps` | `dict[str, str]` | Last status per step. |
| `state` | `Literal["publishing","published","removing"]` | Lifecycle; a removed name is deleted from the file. |
| `updated_at` | `str` | ISO-8601 UTC. |

### Lifecycle

```text
(absent) --add--> publishing --verify ok--> published
publishing --add rerun--> publishing | published
published --renew--> published
published|publishing --remove--> removing --all removal steps done--> (absent)
removing --remove rerun--> removing | (absent)
```

The state file is written after every step, so a crash leaves the last
completed step recorded.

## RemoteInventoryEntry (from the remote program)

| Field | Type |
|---|---|
| `fqdn` | `str` |
| `block_present` | `bool` |
| `header` | `{"fqdn","upstream","tailnet_ip"} \| None` (parsed from the block's header comment) |
| `cert_present`, `key_present` | `bool` |
| `cert_not_after` | `str \| None` |
| `cert_sha256` | `str \| None` |
| `cert_names` | `list[str]` (subjectAltName DNS entries) |
| `key_matches` | `bool \| None` |

## DnsResult (from the Cloudflare child)

| Field | Type |
|---|---|
| `result` | `Literal["created","exists","absent","deleted","conflict","error"]` |
| `record_id` | `str \| None` |
| `content` | `str \| None` |
| `proxied` | `bool \| None` |
| `code` | `str \| None` |
