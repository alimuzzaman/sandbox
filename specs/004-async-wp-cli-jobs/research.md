# Research: Async / Background WP-CLI Jobs

## Decision: accepted supervisor per driver

- **Docker**: start one `start_new_session=True` host supervisor, durably record
  `launch:<pid>`, then let that live supervisor run the existing named
  `docker compose run -d` operation. On success it atomically changes the
  handle to `container`; on failure or signal it removes the exact named
  container and records a terminal outcome only after exact absence is observed.
- **Herd (host)**: start the pinned-PHP WP wrapper in a new session and durably
  record its process-group leader before returning.
- **Rationale**: measured Docker container creation exceeded SC-001 on the local
  stack. The supervisor is real running work, not a queued descriptor, and
  gives immediate poll/kill a stable ownership boundary while preserving the
  already-proven named WP-CLI container path.
- **Alternatives**: wait for `compose run -d` (misses SC-001); return a passive
  queued record (not accepted work); execute inside the web container (not all
  server images carry the WP-CLI/database client parity of the wpcli service).

## Decision: Sandbox records verified internal cancellation handles

- The Herd wrapper and Docker launch supervisor are process-group leaders created
  by Sandbox with `start_new_session=True`. Sandbox durably writes the returned
  process handle before returning. Docker changes that
  handle to the literal `container` only after the named container is accepted.
- Before normal handle publication, Sandbox writes an internal cleanup receipt.
  It binds `PID == PGID`; Docker also binds the exact derived container name.
  Normal publication removes it. If publication fails, later status/kill calls
  validate the receipt and process identity, retry TERM/KILL for the whole group,
  and for Docker retry exact container removal.
- **Cancel** signals only an identity-checked group or removes the exact named
  container. Probe timeout, malformed output, or transport error is unknown and
  cannot authorize completion. A launch-marker job with no container is also
  unknown until explicit cleanup proves both boundaries absent.
- **Rationale**: no PID is public or accepted from the WP command. The host owns
  the Popen identity and checks PID, PGID, and exact job name before signalling.
  Leader exit alone is insufficient: the entire PGID must be observed absent.
  A stored PID/PGID is not current identity proof: mismatch or unknown identity
  refuses signalling even when that numeric group exists, preventing PID-reuse kills.
- **Alternatives**: `exec wp` (makes `$$`==wp but then can't capture the exit code); parse `docker top` (brittle); no-cancel (rejected — clarification put cancel in v1).

## Decision: file-based state machine (no DB/registry)

- `job_<id>.log` (combined stdout+stderr, appended live) + `job_<id>.status`
  (exists only after terminal certainty; contents = exit code; `143` if killed)
  + internal `job_<id>.pid` handle.
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

## Local evidence

Measured command output is retained in
[`evidence/local-docker-timing-2026-08-28.md`](./evidence/local-docker-timing-2026-08-28.md).
It is local evidence for review, not remote or independent acceptance.
