# Research: Async / Background WP-CLI Jobs

## Decision: accepted supervisor per driver

- **Docker**: start one `start_new_session=True` host supervisor, durably record
  `launch:<pid>`, then let that live supervisor run the existing named
  `docker compose run -d` operation. On success it atomically changes the
  handle to `container`; on failure or signal it records a terminal outcome and
  removes the exact named container.
- **Herd (host)**: start the pinned-PHP WP wrapper in a new session and durably
  record its process-group leader before returning.
- **Rationale**: Docker container creation was a fixed ~7-second acceptance
  barrier. The supervisor is real running work, not a queued descriptor, and
  gives immediate poll/kill a stable ownership boundary while preserving the
  already-proven named WP-CLI container path.
- **Alternatives**: wait for `compose run -d` (misses SC-001); return a passive
  queued record (not accepted work); execute inside the web container (not all
  server images carry the WP-CLI/database client parity of the wpcli service).

## Decision: wrapper self-reports its PID (enables cancel)

- The Herd wrapper and Docker launch supervisor are process-group leaders. The
  host durably writes their exact handle before returning. Docker changes that
  handle to the literal `container` only after the named container is accepted.
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
