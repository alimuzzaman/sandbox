# Data model: Front-door-only refresh

## Target record additions (`hosts.json`, `hosts[<remote>/<project>/<env>]`)

| Field | Written by | Shape |
|---|---|---|
| `front_door_declaration` | successful apply, edge continuation, refresh | `"sha256:<64 hex>"`, digest of the non-secret front-door declaration |
| `front_door_refresh` | refresh (start and end) | `{"state": "started"\|"refreshed"\|"unchanged"\|"failed", "at": int, "route_before": digest\|null, "route_after": digest\|null, "code": str\|null, "restore": "restored"\|"removed"\|"restore_failed"\|null}` |

`restored`: the previous route is back. `removed`: a private target whose previous route was
not a marked private route (unreachable after the FR-007 check; kept because the shared
restore helper can return it). `restore_failed`: the restore raised; reported with the
failure. Refusals are not written to the record (they change nothing); they go to the audit
log.

No other field is written by a refresh. `edge`, `runtime`, `hosting_operation`,
`requested_revision`, `staged_revision`, `records`, `certificate` stay as they are.

## Front-door declaration (digest input)

From the validated manifest: `project`, `environment`, `routes`, `cloudflare.proxied`,
`basic_auth` without the password value (username, password secret name, bypass lists),
`login_service` (the share/gate declaration; it carries secret names, never values),
`indexing`, `robots`, `dns_coverage` (mode, zone), `healthcheck.path`.

## Refresh plan (read-only answer, also the first half of a run)

`{"ok": true, "action": "plan"|"refreshed"|"unchanged", "target": "P/E", "front_door": "caddy"|"nginx",
"route_name": str, "loopback_port": int, "private": bool, "gate_hostnames": [..],
"hostnames": [..], "route_before": digest, "route_after": digest|null, "legacy_binding": bool}`

## Refusal codes (stable)

| Code | When |
|---|---|
| `front_door_refresh_fenced` | active operation, uncertain recovery, active image activation, destroy fence |
| `host_apply_lease_held` / `host_apply_lease_unavailable` | remote target lease (existing codes) |
| `front_door_refresh_no_route` | no successful delivery on record, no installed route, invalid recorded port |
| `front_door_refresh_declaration_changed` | names, records, proxy, port, front-door kind, route name, coverage or declaration digest differ |
| `front_door_refresh_certificate_missing` | certificate absent or not covering the names |
| `front_door_refresh_privacy_unproven` | private manifest, installed route not a marked private route |
| `front_door_refresh_would_publish` | marked private route installed, manifest public |
| `front_door_refresh_auth_weakened` | installed route has basic auth, rendered route has none |
| `front_door_refresh_https_lost` | installed route serves HTTPS, rendered route does not |
| `front_door_refresh_names_changed` | the rendered route would serve a different set of hostnames than the installed route |
| `front_door_refresh_upstream_changed` | the rendered route would proxy to other loopback ports than the installed route |
| `host_target_unknown` / `host_target_invalid` | `--name`/`--target` resolution (existing codes) |
| `private_proxied_record` | existing spec 081 check |
| `front_door_refresh_failed` | reload or verification failed (with restore outcome) |
| `front_door_refresh_secrets_missing` | a declared secret is unavailable |

## Audit line (`$SANDBOX_HOME/runtime/front-door-refresh/audit.jsonl`, 0600)

`{"at", "op": "start"|"end", "controller", "remote", "target", "front_door", "outcome", "code",
"route_before", "route_after", "restore"}`. Digests only; no route text, secret or address.
