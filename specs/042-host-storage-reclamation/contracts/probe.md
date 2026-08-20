# Contract: shipped probe additions

The probe program in `sandbox/resources/remote.py` (`_REMOTE_PROGRAM`) is shipped from the
operator's machine on every call, so these additions require no host runtime upgrade. The
same source is executed locally for local targets.

## `action: "observe"` — new `reclaim` block

An `observe` response gains a top-level `reclaim` object alongside `resources`:

```jsonc
{
  "identity": "…", "capacity": {...}, "resources": [...],
  "reclaim": {
    "deployment_root": "…",
    "runtime_root": "…",
    "entries": [{
      "name": "…", "path": "…", "size_bytes": 123, "size_state": "measured",
      "mtime": 1755300000.0, "is_workspace": true, "is_symlink": false,
      "containers": [{"name": "…", "running": false, "id": "…"}],
      "registry": false, "indexed": false, "hosted": false,
      "active_job": false, "protections": []
    }],
    "volumes": [{"name": "…", "size_bytes": null, "mounted_running": false,
                 "project": "…"}],
    "leases": {"<name>": {"expires_at": "…", "released": false, …}},
    "hosted_sites": ["…"],
    "index_names": ["…"],
    "truncated": false,
    "unmeasured_count": 0,
    "status": "complete|partial|unavailable"
  }
}
```

The probe reports raw evidence only. Classification, protection, and tier selection are
decided by `sandbox/resources/reclaim.py` on the operator's machine, so policy can be unit
tested and changed without redeploying anything.

## `action: "reclaim"`

Request:

```jsonc
{
  "action": "reclaim",
  "run_id": "<32 hex>",
  "trigger": "manual|threshold|reap",
  "budget_seconds": 900,
  "candidates": [
    {"seq": 1, "kind": "worktree", "locator": "/abs/path", "bytes": 123,
     "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
     "expected_mtime": 1755300000.0,
     "stop_containers": ["container-id"]}
  ]
}
```

Response (single JSON object):

```jsonc
{
  "stage": "final",
  "run_id": "…",
  "manifest_path": "$SANDBOX_HOME/runtime/resources/deletions/<run_id>.jsonl",
  "outcomes": [{"seq": 1, "locator": "…", "status": "removed|already_absent|skipped|failed|timed_out",
                "reason": "…", "bytes": 123, "elevated": false,
                "verified_absent": true}],
  "reconciled": {"registry_removed": 0, "index_removed": 3},
  "capacity_before": {...}, "capacity_after": {...},
  "budget_exhausted": false
}
```

Guarantees the probe enforces host-side, before it removes anything:

1. The locator resolves strictly inside the deployment root or the runtime root, is not one
   of those roots, is not the sandbox home, and is not a symlink.
2. The locator is not inside the hosted-sites subtree.
3. For `kind: "volume"`, the name matches the workspace-scoped disposable pattern and no
   running container mounts it. Any other volume name is refused with
   `volume_not_workspace_scoped` even if the operator asked for it.
4. The current `mtime` equals `expected_mtime`; otherwise the candidate is skipped with
   `candidate_modified_since_plan`.
5. The manifest directory is created and the `intent` record is written and `fsync`ed
   **before** the removal begins.
6. On `PermissionError`/`EPERM`, removal is retried once through bounded
   `sudo -n timeout -k 1 N rm -rf --`; afterwards the path is re-stat'ed. If it still
   exists, the outcome is `failed` with `partial_removal_detected` — never `removed`.
7. An `outcome` record is appended after each candidate.

The action is resumable and idempotent: a candidate whose path is already gone returns
`already_absent`, and the run may be re-issued with the same `run_id` (the manifest is
append-only, so a resumed run appends rather than truncates).

## `action: "lease"`

Request: `{"action": "lease", "op": "get|set|release|list", "name": "…",
"expires_at": "…", "budget_seconds": 20}`.

Response: `{"stage": "final", "leases": {…}, "ok": true}`. Lease files live in
`$SANDBOX_HOME/runtime/resources/leases/<name>.json`, are written atomically with mode
`0600`, and `name` must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$` (no path separators).
