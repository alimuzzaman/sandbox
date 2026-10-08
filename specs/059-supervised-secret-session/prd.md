# Product Requirements Draft: Supervised Long-Running Secret Session

**Status**: Ready

**Created**: 2026-10-08

**Last Refined**: 2026-10-08

**Input**: "Feedback 2cfab06f: `secrets run` caps the child at 30 minutes, so a local dev server cannot run for a working session with a brokered secret. Wanted: a supervised long-running mode, without the secret ever being printed or persisted."

**Drafting Configuration**: Claude Opus 5.5 root drafting.

**Final Validation**: `PASS` — independent read-only review, Claude Fable 5.1 (default effort); final wording edits applied as directed by the reviewer

**Validated On**: 2026-10-08

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

A developer runs a local dev server (for example `flask run`) that needs one
API key. The sanctioned way to hand a secret to a child process is
`sb secrets run`, which spec 041 bounds at 30 minutes (FR-036: default 5
minutes, explicit 1 second to 30 minutes, process group terminated at the
limit). The server dies mid-session and has to be relaunched. The only
alternative, writing the key into a project `.env`, persists it to disk, which
is the higher-disclosure outcome the broker exists to prevent.

The 30-minute cap is a lease: the broker has no other way to bound how long a
secret recipient lives. A longer-lived mode therefore needs a different bound
that the broker enforces, not just a bigger number. `secrets run` also returns
the child's output only after it exits, which suits a bounded command but not a
server whose logs matter while it runs.

Passing the child's own flags after `--` (the second half of the feedback)
already works today and is out of scope.

## Users and Desired Outcomes

- **Local developer**: run a dev server with a brokered secret for a working
  day, started once from a terminal, without the value on disk or on screen,
  watching its logs live.
- **Secret owner**: the secret's exposure stays bounded and attributable even
  when the child runs for hours.

## Goals

- A local, explicitly requested session mode keeps one child alive with
  brokered secrets past 30 minutes, up to a broker-enforced lifetime.
- The session ends when its lifetime expires, when the child exits, or when the
  broker process receives an interrupt or hangup.
- Every argument check (lifetime range, destination deny list, terminal check)
  completes before any secret value is read.
- All spec 041 run guarantees otherwise apply unchanged: no value in argv,
  output, errors, audit or the parent environment; minimal child environment;
  destination deny list; redacted output.
- Documentation marks session mode as operator-run from a terminal; agents keep
  using bounded `secrets run`.

## Non-Goals

- Raising the cap of ordinary `secrets run`.
- MCP, durable-job or remote use of the session mode.
- Background daemons that outlive the broker, restarting a crashed child, or a
  separate status/stop command.
- Interactive input to the child; its standard input is closed as in
  `secrets run`.
- Rotating or re-reading the secret during a session.
- Network-exposure detection for the child (the broker cannot see what the
  child binds to).

## Product Scenarios

### Scenario 1 — Start a session

- **Starting state**: a registered secret source and key; an interactive
  terminal.
- **User action**: the developer starts a session for a dev-server command,
  optionally with an explicit lifetime.
- **Expected outcome**: the child runs with each selected secret in its
  destination variable; redacted output appears live in the terminal; the session states
  its lifetime and when it will end.

### Scenario 2 — Session ends

- **Starting state**: a session is running.
- **User action**: Ctrl-C, closing the terminal, or nothing until the lifetime
  expires (or the child exits by itself).
- **Expected outcome**: the child's process group ends; the end reason is one
  of `lifetime_expired`, `interrupted`, `hangup`, `child_exited`, reported and
  audited without any value-derived data. On `child_exited` the broker exits
  with the child's exit status, as `secrets run` does.

### Negative scenarios

- No terminal (CI, MCP, durable job, redirected output): refused with
  `tty_required` before any secret is read.
- Lifetime below 1 second, not a whole number, or above 12 hours: refused
  before any secret is read.
- Both a session lifetime and the ordinary run timeout given: refused.
- Destination on the deny list or unknown source: refused before any secret is
  read. Unknown key: refused as in `secrets run`, with no value delivered to
  any child.
- The child exits early: the session ends with `child_exited`; it is not
  restarted.
- Redaction of an output chunk fails: that chunk is dropped, not shown, and the
  number of dropped chunks is reported at the end.

## Proposed Product Behavior

- Session mode is an explicit opt-in on the CLI, local only, foreground only.
- Lifetime is given in whole seconds, defaults to 8 hours, is set with one explicit option, and may not
  exceed 12 hours; there is no unbounded value.
- Redacted child output is streamed to the terminal as it is produced and is
  not retained in the result; the result carries metadata only. Audit follows
  `secrets run`.

## Constraints and Dependencies

- Amends spec 041 FR-036 and the display clause of FR-037 for this mode only.
  FR-030 to FR-035 and FR-041 hold. Nothing is retained, so FR-037's retention
  cap is moot; FR-038 elapsed-time classes are extended to cover hours.
- Local secret sources registered with `sb secrets`.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Ordinary `secrets run` cap | Unchanged | Spec 041 FR-036 | Existing policy |
| Scope | Local CLI only, no MCP | FR-039 forbids arbitrary MCP commands | Existing policy |
| Keep the mode | Yes, as its own spec | Only alternative is a `.env` on disk, which is worse | Fable decision (delegated by user), 2026-10-08 |
| Lifetime | Default 8 h, maximum 12 h, one explicit option | A working day; beyond that is a daemon | Fable decision (delegated by user), 2026-10-08 |
| Output | Live redacted stream, nothing retained | Dev-server logs matter live; the retained cap stays | Fable decision (delegated by user), 2026-10-08 |
| Control | Foreground only, no status/stop command | Avoids persisted session state and a second entry point | Fable decision (delegated by user), 2026-10-08 |

## Open Questions

- None.

## Acceptance Outcomes

- A dev server started in session mode keeps serving past 30 minutes and is
  terminated at its lifetime with reason `lifetime_expired`, its process group
  gone within 5 seconds of expiry (verified with a
  short test lifetime and with a run past 30 minutes).
- An interrupt or hangup of the broker ends the child's process group within 5
  seconds.
- Across a full session, the secret value appears in 0 places among argv,
  terminal output, the result, audit, errors and files written by Sandbox.
- A start is refused with `tty_required`, before any secret is read, whenever
  no terminal can be opened or standard output is not a terminal.
- Every refused start delivers no secret value to a child; refusals for
  argument, terminal, destination and source errors happen before any secret
  is read.

## Risks and Assumptions

- **Risk**: a longer-lived child is a longer-lived secret recipient (spec 041
  FR-041); the child can still print or persist the value itself.
- **Risk**: if the broker is killed uncatchably, the child outlives it; its
  lifetime is then bounded only by its own exit, and the audit record keeps the
  intent but no outcome.
- **Assumption**: interrupt and hangup delivery to the broker is a meaningful
  bound for interactive local work.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
