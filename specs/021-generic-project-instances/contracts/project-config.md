# Contract: Generic Project Configuration

## Backward compatibility

Any current `sandbox.config.*` without `kind` is interpreted exactly as `kind: "wordpress"`. `.wp-env.json` always resolves to WordPress. WordPress plugin slug validation is applied only after WordPress kind selection.

## Minimal explicit Compose project

```json
{
  "kind": "compose",
  "name": "alimuzzaman.me",
  "runtime": {
    "composeFile": "compose.yaml",
    "service": "web",
    "containerPort": 4321,
    "healthPath": "/"
  }
}
```

Required behavior:

- Unknown top-level kinds fail with supported values.
- `composeFile` resolves relative to the project root and cannot escape it.
- The declared service and port are validated against `docker compose config` before mutation.
- Generic configuration does not inherit user-global WordPress plugins, themes, mappings, versions, admin credentials, database, mail, multisite, or WP configuration.
- A per-label configuration may change safe adapter settings but cannot change `kind`.
- Secrets remain in existing machine-local secret/override locations or project-owned Compose mechanisms; Sandbox never copies them into its registry or generated overlay.

## Astro initialization output

The Astro preset writes an explicit Compose descriptor. When no project Compose file is selected, it proposes `sandbox.compose.yml` containing a conventional Node development service, source bind mount, reviewed package command, `0.0.0.0` bind, and declared internal port. The generated file is project-owned and reviewable; ensure does not regenerate it silently.
