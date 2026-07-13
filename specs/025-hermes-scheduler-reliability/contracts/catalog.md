# Contract: Sandbox Hermes Cron Catalog

The catalog is a committed JSON object:

```json
{
  "schema_version": 1,
  "jobs": [
    {
      "name": "stable-name",
      "schedule": "every 30m",
      "kind": "script",
      "script": "example.py",
      "prompt": "",
      "profile": null,
      "workdir_template": null,
      "enabled": true,
      "deliver": "local"
    }
  ]
}
```

Rules:

- Names are unique and become reconciliation identities; upstream IDs are observations only.
- Script filenames are basenames present under `sandbox/hermes/cron_scripts/`.
- Agent entries require a named Sandbox route. Free-form provider/model/effort fields are forbidden.
- Workdir templates may reference only declared remote path keys and resolve to normalized absolute paths.
- Catalog and scripts must contain no secret-like assignment or credential material.
- A normalized catalog and script-content fingerprint is included in previews and applied-state evidence.
- Base catalog scripts never add, remove, pause, resume, or edit cron jobs themselves.
