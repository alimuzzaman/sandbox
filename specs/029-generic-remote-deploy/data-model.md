# Data Model: Generic Remote Deploy

## Project descriptor

| Field | Meaning | Validation |
|---|---|---|
| `kind` | Runtime selection | `wordpress` or explicit `compose` |
| `service` | Generic public service | Existing declared service validation |
| `internal_port` | Service listener inside Compose | Existing valid port requirement |
| `health_path` | Reachability condition | Existing absolute-path validation |

## Remote instance result

| Field | WordPress | Generic Compose |
|---|---|---|
| `kind` | `wordpress`/legacy omitted | `compose` |
| `instance` | Required | Required |
| routed port | `wordpress_port` | `http_port` |
| activation | Plugin activation | None |
| public URL update | WordPress home/siteurl | None |
| `url` | Rewritten after exposure | Replaced in response after exposure |

The result retains `{ok, remote, pushed_commit, uncommitted_files_applied, instance,
url, error}` for both kinds.
