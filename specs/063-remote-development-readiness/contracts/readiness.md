# Contract: Remote readiness

## CLI and MCP

```
./sb remote readiness [<remote>] [--project-dir DIR] [--json]
MCP: remote_readiness(project_dir, remote=None)
```

Both return the same envelope:

```json
{"ok": true, "action": "readiness",
 "data": {"remote": "xcloud-london", "remote_selection": "profile",
          "rows": [{"aspect": "registration", "state": "ready"},
                   {"aspect": "reachability", "state": "ready"},
                   {"aspect": "runtime_compatibility", "state": "ready"},
                   {"aspect": "capacity", "state": "not_ready",
                    "reason": "missing_pool_evidence",
                    "remedy": "./sb remote network-range propose xcloud-london"},
                   {"aspect": "ownership_repair", "state": "not_applicable",
                    "reason": "no_instance"},
                   {"aspect": "handoff", "state": "unknown",
                    "probe_state": "unrecorded",
                    "remedy": "./sb ensure --remote xcloud-london --project-dir DIR"}],
          "installed_runtime_revision": "…", "taken_at": 0,
          "reusable_until": 300}}
```

- `ok` is false only when the check itself cannot run (a bad project dir).
  `not_ready` rows are data, not errors.
- Every remedy parses against the CLI, checked as 061 refusals are.

## Submission gate

`sandbox.readiness.gate.require_ready(project_dir, remote_hint, *, purpose)`
is called before any transfer by: `test`/`run_tests`, `e2e`/`run_e2e`,
`ci`/`ci_run`, `exec --remote`, `job-start` and `ensure --remote`.

- It reuses a proof when `now < reusable_until` and the installed revision is
  unchanged; otherwise it runs the check.
- On the first `not_ready` row it raises `RemoteNotReady(row)`. The command
  exits non-zero with error code `remote_not_ready_<aspect>`, the row's
  reason and remedy, and `bytes_transferred: 0`.
- `unknown` and `not_applicable` rows never refuse.
- The capacity admission still runs afterwards and still allocates.

## Selection refusals

- `unknown_remote` data: `{name, name_source: caller|declaration, registered:
  [names], remedy}`.
- `ambiguous_remote` data: `{candidates: [names], remedy}`.
- A local run requires `--local`. When the declared remote was `not_ready`,
  the output includes `{declared_remote, failing_aspect, reason}` and
  `remote_selection: local`.
