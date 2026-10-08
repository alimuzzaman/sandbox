# Contract: published manifest provenance for promoted captures

The manifest stays spec 023 schema 1 (`contracts/manifest-v1.md` in spec 023);
`verify_manifest` (`sandbox/recovery/restore.py:54`) is unchanged. Promote adds
one object under `provenance`:

```json
"provenance": {
  "remote": "R", "machine_identity": "R:host", "revision": "<rev>",
  "source_digest": "sha256:…", "capture_contract_version": 2,
  "backup_operation_id": "B",
  "server_capture": {
    "schema_version": 1,
    "request_id": "recovery-<64 hex>",
    "backup_operation_id": "B",
    "archive_sha256": "<64 hex>",
    "archive_size": 123,
    "members": [{"name": "database.sql", "sha256": "…", "size": 1},
                {"name": "wordpress.tar", "sha256": "…", "size": 1}],
    "declarations_sha256": "<64 hex>",
    "inventory_summary": {"table_count": 0, "view_count": 0, "rows_estimate_total": 0},
    "captured_at": [started_at, completed_at],
    "promoted_at": "<ISO 8601 UTC>"
  }
}
```

Rules:

- `artifacts[]` contains `amarsonar-bangla-prod/amarsonar-bangla.tar` whose
  `sha256` equals `server_capture.archive_sha256` (SC-006 check, no decryption).
- A second promote recognizes its own set by `server_capture.request_id` and
  `archive_sha256`; any other manifest under the same set id is `set_id_conflict`.
- A pending set finished without a sidecar adds `"pending_recovered": true`
  and has no `server_capture` object.
- No secret, credential, host path or passphrase-derived value is recorded.
