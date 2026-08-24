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
| provision, service install/migrate/stop/status, recovery | SSH lifecycle | retained recovery/bootstrap exception |
| source upload, plugin mirror, bulk staging | tar/rsync/SSH | retained staging exception |
| jobs, workspace control, ensure/status/logs | SSH controller today | next migration slice |
| domains, Docker-pool control, deploy/hosting/preview | mixed SSH and local control | next migration slice |
| Hermes and recovery inventory | SSH-backed adapters | next migration slice; recovery remains an exception |

“Migrated” means the normal code path is typed, authenticated, bounded, and service
backed. It does not mean that SSH has been removed from lifecycle or source-transfer
recovery paths. The operator command is deliberately explicit:

```sh
./sb remote ssh <remote> --confirm --reason "diagnose service" --command 'systemctl --user status sandbox-remote-mcp'
```

Do not use that command as an automatic retry or to bypass a service revision mismatch.
