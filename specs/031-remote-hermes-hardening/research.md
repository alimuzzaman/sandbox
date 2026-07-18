# Research: Remote and Hermes Operations Hardening

## Decision: use a systemd user service and owner-only environment file

**Rationale**: The current remote MCP launcher embeds a bearer token in `sb mcp`
arguments and runs it via `setsid`. A user service gives service identity, enablement,
bounded restart, cgroup-scoped stop, and reboot recovery through user lingering. An
owner-only environment file keeps the secret out of unit text and argv while requiring
no new platform dependency.

**Alternatives considered**:

- Continue PID-file + `setsid`: rejected because it has no service ownership or reboot
  contract and currently leaks the token through argv.
- systemd credentials: potentially stronger, but target support and test fixtures are
  less uniform; keep it as a future compatible implementation option.
- process discovery by flags: rejected because generic flags do not prove ownership.

## Decision: only selected-unit control may mutate the remote MCP service

**Rationale**: `/proc` scanning for `--transport streamable-http --token` can match an
unrelated process. Unit name, non-secret marker, expected bind/port, staged runtime,
and cgroup/PID evidence form the minimum ownership proof.

**Alternatives considered**:

- Match a more specific process command: rejected because it remains untrusted argv
  evidence and does not establish ownership.
- Keep process scan as fallback stop: rejected because it preserves the dangerous
  behavior. Legacy process detection is read-only only.

## Decision: health uses individual component facts and reason codes

**Rationale**: Current health treats inactive gateway/disabled linger/scheduler gaps
as indirect or invisible. Independent facts allow operators to distinguish recovery,
gateway, scheduler, catalog, session, and worktree failures.

**Alternatives considered**:

- One generic degraded reason: rejected because it cannot guide safe repair.
- Treat unknown as healthy: rejected because it creates false operational confidence.

## Decision: retain fail-closed fingerprints and add transaction rollback

**Rationale**: A fingerprintless scheduler cannot prove ownership. An exact plan,
preflight snapshot, replacement, postcondition, and restore provides a reviewable
migration without silently enabling autonomous work.

**Alternatives considered**:

- Automatically repair legacy jobs: rejected because it changes agent activation
without approval.
- Backup only: rejected because it cannot recover a partial replacement.

## Decision: classify documented terminal markers only after valid evidence

**Rationale**: `COMPLETED_SPEC_TASK` can be valid work wrapped as an error. The
classifier must require a recognized marker plus valid terminal transition and must
allow provider/client failures to win.

**Alternatives considered**:

- Treat all non-empty output as success: rejected because it masks failures.
- Ignore wrapper behavior: rejected because it leaves false failures unexplainable.
