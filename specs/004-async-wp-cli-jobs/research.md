# Research: Async / Background WP-CLI Jobs

## Decision: detached launch per driver

- **Docker**: `docker compose exec -d -w <ABSPATH> wpcli sh -c '<wrapper>'` — `-d` backgrounds inside the container and returns immediately.
- **Herd (host)**: `cd <wp_root> && nohup sh -c '<wrapper>' >/dev/null 2>&1 & echo $!` using the instance's pinned `php<MM>` + `wp` shims.
- **Rationale**: both reuse the existing driver helpers; neither blocks the caller.
- **Alternatives**: a long-poll sync call with a big timeout (still blocks, still caps); a daemon/queue (overkill for dev).

## Decision: wrapper self-reports its PID (enables cancel)

- The launch wrapper is started with **`setsid`** so its `$$` is the process-**group** leader; it writes `echo $$ > .sb-jobs/job_<id>.pid` first, runs `wp …`, then `echo $? > .sb-jobs/job_<id>.status` on exit.
- **Cancel** sends `kill -TERM -$(cat …pid)` (negative PID = the whole group), so the child `wp`/`php` processes are terminated too — no orphans (analysis F6). Container: via `compose exec`; herd: directly.
- **Rationale**: detached `compose exec -d` doesn't cleanly surface the inner PID, so the start response returns **no `pid`** (analysis F5); the self-reported group-leader `$$` in `.pid` is the reliable cancel handle. `setsid` guarantees `$$` == PGID.
- **Alternatives**: `exec wp` (makes `$$`==wp but then can't capture the exit code); parse `docker top` (brittle); no-cancel (rejected — clarification put cancel in v1).

## Decision: file-based state machine (no DB/registry)

- `job_<id>.log` (combined stdout+stderr, appended live) + `job_<id>.status` (exists ⇒ done; contents = exit code; `143` if killed) + `job_<id>.pid`.
- **Rationale**: lock-free, host-readable directly from the bind-mount, survives across calls; presence/absence is the running→completed signal. Matches the proven Novamira pattern.

## Decision: incremental output via byte offset

- `wp_cli_job(offset, limit)` fseeks to `offset`, reads `limit` bytes, reports `bytes_read` + `truncated`; `limit=-1` = whole file.
- **Rationale**: large logs aren't re-sent each poll.

## Decision: prune by age (default 24h)

- `./sb jobs --prune` and an age-based auto-prune on `jobs`/instance-up remove old `.log`/`.status`/`.pid`.
- **Rationale**: artifacts are gitignored runtime state; bound disk without manual cleanup.

## Decision: job_id format + validation

- 16 hex chars (`bin2hex`/`os.urandom(8)`), validated `^[a-f0-9]{16}$` before any path use.
- **Rationale**: prevents path traversal via a forged id (the only untrusted input that hits the filesystem).

## Open questions

None — cancellation resolved in v1 (clarification); no concurrency cap / SSE in v1.
