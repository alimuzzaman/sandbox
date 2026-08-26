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

## 2. Poll to completion (incremental)

- `wp_cli_job(job_id, project_dir=…)` → `status:"running"` with partial `stdout`.
- Poll with advancing `offset`; confirm only new bytes return (`bytes_read`, `truncated`).
- After it finishes → `status:"completed"`, `exit_code:0`, full log re-readable.

## 2a. Replay an uncertain acceptance safely

Use a stable identity when the caller may need to retry the acceptance call:

```
./sb wp --async --request-id wp-request-1 -- option get siteurl
```

An identical second call returns the same `job_id` and launches no second
process. Reusing `wp-request-1` for different WP-CLI args fails closed. If the
first call reports `acceptance_unknown`, inspect that job with `./sb job` before
retrying; do not invent a new request ID to bypass the uncertainty.

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

Repeat steps 1–3 on a **herd** instance (host `nohup` path) and confirm identical
behavior.

## 6. Safety

- A forged `job_id` (not `^[a-f0-9]{16}$`) is rejected before any filesystem access.
- `async` runs only `wp` — it does not accept commands the sync path wouldn't.

## 7. Disposable Docker evidence — 2026-08-26

This acceptance run used a temporary per-worktree `SANDBOX_HOME` and a disposable
WordPress instance. It did not use or change the shared instance registry, existing
instances, remote hosts, or production state.

- First post-ensure Nginx `web-exec` launch: client acceptance **1,270.243 ms**;
  private receipt **330.449 ms**; job `46f4262c1d3031b5`; exit `0`.
- Three warm Nginx `web-exec` launches: client acceptance **1,323.606 ms**,
  **1,194.099 ms**, and **1,209.229 ms**; private receipts **370.227 ms**,
  **292.788 ms**, and **337.128 ms**; jobs `d7d29f3358ca49cc`,
  `a323e9a59f9699ac`, and `b5c8b37cb09c006a`; all exit `0`.
- Two `wp db` compatibility `run` launches: client acceptance **1,974.677 ms**
  and **1,918.533 ms**; private receipts **1,051.358 ms** and **1,009.751 ms**;
  jobs `ff0e2bbd2b9cb0d8` and `c9f04ca5853dcde7`; both exit `0`.
- Polling re-read the retained `t021-warm` and database output. Job
  `23b647678e8a0637` was cancelled and completed with exit `143`; its
  pre-cancel marker remained and its post-cancel marker did not appear.

All measured client acceptances were below two seconds. This proves the current
Nginx shared and database-fallback paths only. Replay-safe duplicate-request
behavior is covered by the focused fixture suite; LiteSpeed,
older/stopped-service, and cold-Docker-daemon paths still need evidence before
SC-001 or all-tier parity can be marked complete. A disposable LiteSpeed
attempt on 2026-08-26 reached container creation but timed out in Sandbox's
bounded 30-second document-root bootstrap check; no LiteSpeed async launch was
observed.
