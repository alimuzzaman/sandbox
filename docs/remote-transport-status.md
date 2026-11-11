# Remote transport status

The control-plane migration is staged. Normal host diagnostics, resource probes,
resource cleanup/revalidation, and dashboard inventory use the authenticated remote
service. They fail closed when the service is unavailable; they do not fall back to
SSH.

| Surface | Current transport | Boundary |
| --- | --- | --- |
| `remote service diagnostics`, process/container view | authenticated service | migrated |
| `remote resources` observe/reclaim/lease/revalidate/remove | authenticated service | migrated |
| `./sb web` remote inventory and bounded deep refresh | local loopback BFF → authenticated service | migrated |
| `remote ssh` | explicit operator CLI command | escape hatch only; never internal/MCP |
| `remote join`, `remote service refresh` | authenticated service identity discovery | local registration only; no service changes |
| provision, service install/migrate/stop/status, recovery | SSH lifecycle | retained recovery/bootstrap exception |
| source upload, plugin mirror, bulk staging | tar/rsync/SSH | retained staging exception |
| jobs, workspace control, ensure/status/logs | SSH controller today | next migration slice |
| domains, Docker-pool control, deploy/hosting/preview | mixed SSH and local control | next migration slice |
| Hermes and recovery inventory | SSH-backed adapters | next migration slice; recovery remains an exception |

“Migrated” means the normal code path is typed, authenticated, bounded, and service
backed. It does not mean that SSH has been removed from lifecycle or source-transfer
recovery paths. The operator command is deliberately explicit:

Joining records the admitted service's ownership marker, runtime revision and
canonical machine identity from the existing schema-1 host-memory status contract.
An existing joined controller with missing metadata can run:

```sh
./sb remote service refresh NAME --json
```

This writes only the local registration. It uses tailnet admission without an
owner bearer token or SSH, validates the complete response, preserves existing
pins and unrelated registration fields, and refuses identity/revision drift or a
concurrent registration change. It does not upgrade the remote, repair SSH, change
stack reservations, or bypass runtime compatibility admission. If the service
revision changed, use the separately reviewed lifecycle path; refresh cannot
replace a pin. A successful join does not imply every SSH operation is available.
Ordinary host-memory status and authenticated target-identity lookup also enforce
the saved machine pin; a changed machine or malformed pin refuses without refresh.
Legacy owner registrations with no machine pin keep their existing behavior.

Every direct `remote ssh` command requires `--confirm`, including read-only commands,
and a short `--reason`.

```sh
./sb remote ssh <remote> --confirm --reason "diagnose service" --command 'systemctl --user status sandbox-remote-mcp'
```

Do not use that command as an automatic retry or to bypass a service revision mismatch.
