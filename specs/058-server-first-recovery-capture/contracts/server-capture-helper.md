# Contract: server capture helper

Program: `sandbox/recovery/server_capture_helper.py`, stdlib only, Python ≥3.8 on
the remote, under 64 KiB. Invoked as
`python3 -c <source> <op> <capture_root> <args…>` through `ssh_process`
(`sandbox/core/_remote.py:853`). `<capture_root>` is
`<resolved remote $SANDBOX_HOME>/runtime/recovery-captures`. Every op writes a
single JSON object on stdout (except the byte payload of `read-chunk`) and
exits 0; failures are `{"ok": false, "code": "<fixed code>"}` with exit 0 so
the caller never parses stderr. No op prints secrets, paths outside the root,
or raw tool diagnostics.

| Op | Args | stdin | Output | Bound |
|----|------|-------|--------|-------|
| `start` | slot, request JSON (≤16 KiB, argv) | line 1: DB password; rest: declarations JSON (≤64 KiB) | `{ok, state, phase, accepted_at, existing}` | returns after fork, <5 s |
| `status` | slot | – | `{ok, request, state, lock_free, receipt_valid, archive_size, residue_bytes}` | ≤64 KiB |
| `list` | – | – | `{ok, slots: [...], legacy: [{name, size}]}` | ≤256 KiB, ≤500 slots |
| `read-receipt` | slot | – | receipt JSON | ≤1 MiB |
| `read-declaration` | slot | – | declarations JSON | ≤64 KiB |
| `read-chunk` | slot, offset, length (≤16 MiB) | – | header line `{offset,length,sha256}` + bytes | length + 256 B |
| `mark-promoted` | slot, marker JSON (argv ≤4 KiB) | – | `{ok, existing}` | idempotent; refuses unless receipt valid |

## `start` semantics

1. Create root (0700, owner check, no symlink). If the slot exists, compare
   `request.json.request_id`: equal → return the current derived state with
   `existing: true` (no lock taken, works while that slot's own job is running);
   differ → `capture_binding_conflict`.
2. Otherwise open `active.lock` with `LOCK_EX|LOCK_NB`; failure → read the
   active slot's request → `capture_in_progress`. After acquiring it, re-check
   that the slot still does not exist (a racing start may have created it) and
   fall back to step 1 if it does.
3. Check `loginctl show-user $USER -p KillUserProcesses` when `loginctl` exists;
   `yes` → `detach_unsupported`.
4. Write `request.json`, `declarations.json`, `state.json{queued}`; open and lock
   `job.lock`.
5. Double fork; grandchild `setsid`, closes stdio, keeps both lock fds and the
   password in memory; first parent prints the result and exits.

## Job phases (grandchild)

`preflight` (R6) → `inventory` (R5) → `dump` (`mariadb-dump --single-transaction
--quick --routines --events --triggers`, stdout to file, 1800 s) → `files`
(`tar -cf - -C /var/www/html --numeric-owner .` to file, 1800 s) → `verify`
(second tar stream piped into SHA-256, compared with the first file's hash;
table-set scan of the dump) → `archive` (`tarfile` with `database.sql`,
`wordpress.tar`; member hashes streamed) → `receipt` (write receipt, then state
`complete`). The whole job has a 3600 s deadline; each subprocess timeout is
`min(1800, remaining)`. Any failure writes `failed` with a fixed reason and
removes work files. Container names and the database user are the reviewed
constants already in `sandbox/transports/remote_recovery.py:516-526,766-768`.

## Memory

All file reads are 1 MiB chunks; no op loads an archive or member into memory.
`read-chunk` reads at most 16 MiB.
