# Quickstart: Async WP-CLI Jobs — live verification

Prerequisites: a running instance (`./sb ensure` / `ensure_instance`). All checks are
live-stack (constitution IV).

## 1. Async start doesn't block

Use a deterministically long command so the assertion is stable regardless of
content (analysis F8):

```
./sb wp --async eval 'sleep(30);'
```
Expect: prints a 16-hex `job_id` in <~2s; the command keeps running.
(MCP: `wp_cli(command="eval 'sleep(30);'", background=true, project_dir=…)` —
the param is `background`, not the reserved word `async`.)

Docker returns after a live isolated launch supervisor and durable running
handle exist; it does not wait for the slower named WP-CLI container creation.
The checked-in local measurement transcript is in
[`evidence/local-docker-timing-2026-08-28.md`](./evidence/local-docker-timing-2026-08-28.md).

## 2. Poll to completion (incremental)

- `wp_cli_job(job_id, project_dir=…)` → `status:"running"` with partial `stdout`.
- Poll with advancing `offset`; confirm only new bytes return (`bytes_read`, `truncated`).
- After it finishes → `status:"completed"`, `exit_code:0`, full log re-readable.

## 3. Cancel

```
./sb wp --async <a deliberately long command>   # capture job_id
./sb job <job_id> --kill
```
Expect: process gone; `./sb job <job_id>` → `status:"completed"`/cancelled with
exit_code `143`. Killing it again → no-op, no error.

## 4. List + prune

- `./sb jobs` lists active/recent jobs with status.
- `./sb jobs --prune` removes old artifacts; re-list shows them gone.

## 5. Driver parity

Repeat steps 1–3 on a **herd** instance (host new-session wrapper) and confirm identical
behavior.

If Docker process/container observation is unavailable, polling must remain
non-terminal and kill must report that termination could not be verified. Never
interpret that result as completion or retry the WP command under a new job ID.

## 6. Safety

- A forged `job_id` (not `^[a-f0-9]{16}$`) is rejected before any filesystem access.
- `async` runs only `wp` — it does not accept commands the sync path wouldn't.
