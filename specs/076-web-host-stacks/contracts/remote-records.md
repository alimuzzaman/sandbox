# Contract: remote records for the panel

Written by the controller over the existing remote connection. The remote runtime service is not involved, so this is not a control-protocol payload and no `CONTROL_PROTOCOL_SPOKEN` bump is needed.

| Path (under the remote `$SANDBOX_HOME`) | Writer | When |
|---|---|---|
| `runtime/hosts/<project>/<environment>/summary.json` | `sb host apply` | after source publish (`applying`), at success (`succeeded`), on failure (`failed`) |
| `runtime/stack-sites/<slug>/<hostname>.json` | `sb deploy --stack` | after a successful exposed deploy |

Rules:

- Mode 0600. The write goes to a temporary sibling, then `mv`, so a reader never sees a partial file.
- Field allow-list only (see data model). A test plants secret values and asserts none reach the written bytes.
- Removal: `sb host destroy` already removes `runtime/hosts/<project>/<environment>`. Site link files outlive their instance; the panel shows `instance_not_found` for them.
- Readers ignore unknown fields and refuse records with `schema != 1`.
