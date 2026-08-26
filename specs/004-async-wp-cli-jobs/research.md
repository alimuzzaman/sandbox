# Research: Async / Background WP-CLI Jobs

## Decision: detached launch per driver

- **Docker**: use `docker compose exec -d -u www-data -T wp sh -c '<wrapper>'`
  when the running web service has the shipped WP-CLI binary. This reuses the
  service and returns at container-exec acceptance speed. Older/stopped or
  LiteSpeed instances use the compatibility `compose run -d wpcli` launcher.
- **Herd (host)**: Python starts `sh -c '<wrapper>'` with
  `start_new_session=True`, using the instance's pinned `php<MM>` + `wp`
  shims.
- **Rationale**: both reuse the existing driver helpers; neither blocks the caller.
- **Alternatives**: a long-poll sync call with a big timeout (still blocks, still caps); a daemon/queue (overkill for dev).

## Decision: wrapper self-reports its PID (enables cancel)

- The launch wrapper writes `echo $$ > .sb-jobs/job_<id>.pid` first, runs
  `wp …`, then `echo $? > .sb-jobs/job_<id>.status` on exit. The shared Docker
  wrapper runs WP-CLI as a child under a TERM trap, so cancellation can signal
  the wrapper without restarting the web container; the compatibility Docker
  path remains container-scoped. Herd uses Python's
  `start_new_session=True`, making the wrapper a process-group leader.
- **Cancel** signals the shared Docker wrapper through `compose exec` or force
  removes the compatibility job container. Herd sends `kill -TERM -$(cat …pid)`
  (negative PID = the whole group), so child `wp`/`php` processes are
  terminated too — no orphans (analysis F6).
- **Rationale**: detached `compose exec -d` doesn't cleanly surface the inner
  PID, so the start response returns **no `pid`** (analysis F5); the
  self-reported wrapper PID in `.pid` is the reliable cancel handle. A private
  `.launcher` marker records whether the job uses the shared web container or
  the compatibility run container.
- **Alternatives**: `exec wp` (makes `$$`==wp but then can't capture the exit code); parse `docker top` (brittle); no-cancel (rejected — clarification put cancel in v1).

## Decision: file-based state machine (no DB/registry)

- `job_<id>.log` (combined stdout+stderr, appended live) + `job_<id>.status` (exists ⇒ done; contents = exit code; `143` if killed) + `job_<id>.pid`.
- **Rationale**: lock-free, host-readable directly from the bind-mount, survives across calls; presence/absence is the running→completed signal. Matches the proven Novamira pattern.

The launcher also atomically writes a private `job_<id>.receipt` after detached
acceptance. It contains only the job ID, launcher kind, wall-clock acceptance
time, and monotonic client-side `acceptance_ms`; it never stores command argv or
output. Polling may surface the measured latency as diagnostic metadata, but a
live cold/warm sample is still required before the SC-001 target is considered
verified.

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
