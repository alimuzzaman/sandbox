# Configuration Contract

Project source and MCP/use authorization are explicit under the common `secrets` key in `sandbox.config.json`, YAML equivalents, and normal machine override layers.

```jsonc
{
  "secrets": {
    "sources": {
      "project-env": {
        "path": ".env.local",
        "mcpModes": ["keys", "metadata", "validate", "masked", "use"]
      }
    },
    "useProfiles": {
      "provider-status": {
        "source": "project-env",
        "key": "API_TOKEN",
        "argv": ["trusted-provider-cli", "status"],
        "destination": "API_TOKEN",
        "timeoutSeconds": 30,
        "maxOutputBytes": 65536,
        "mcp": true
      }
    }
  }
}
```

Rules:

- `sources` and `useProfiles` default to empty objects.
- The built-in `personal` source is not overridden through project config and has no MCP modes by default.
- Source aliases and profile names are lowercase safe slugs.
- Source paths are relative `.env*` paths, contain no traversal, and must resolve inside the project root.
- `mcpModes` is an explicit subset of `keys`, `metadata`, `validate`, `masked`,
  and `use`; omission means no MCP access.
- A use profile references one registered project source/key and uses one fixed direct argv.
- `destination` must be a portable environment identifier outside the dangerous-name policy.
- `timeoutSeconds` is 1–1800; default 300.
- `maxOutputBytes` is 1–1,048,576; default 1,048,576.
- `mcp` defaults false and cannot compensate for a source that does not grant
  the `use` MCP mode.
- Unknown keys, invalid values, duplicate aliases across layers, or unsafe paths fail configuration normalization.
