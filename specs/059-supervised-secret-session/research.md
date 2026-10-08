# Research: Supervised Long-Running Secret Session

Every decision below is grounded in the current code: `sandbox/secrets/runner.py`
(`run_with_secrets`), `sandbox/secrets/service.py` (`SecretService.run_many`,
`_operate`, `_bounded_call`), `sandbox/commands/secrets.py` (`run`, `reveal`,
`_tty_secret`), `sandbox/secrets/models.py` (`RunResult`, limits),
`sandbox/secrets/audit.py`, `sandbox/services/redaction.py`
(`StreamingRedactor`), `skills/secret-inspection/SKILL.md`, spec 041 and the
tests in `tests/test_secret_service.py`, `tests/test_secret_commands.py` and
`tests/test_secret_mcp.py`.

## R1. CLI surface: a flag on `secrets run`, not a new action

- **Decision**: `sb secrets run --session [--lifetime-seconds N] ...`. The
  session reuses `run`'s `--source`, `--key`/`--destination`,
  `--secret KEY=DEST` and `-- ARGV` grammar unchanged.
- **Rationale**: The PRD's negative scenario "both a session lifetime and the
  ordinary run timeout given: refused" only exists if both options live on one
  command. Reusing `run`'s binding grammar (`cmd_secrets` run branch) keeps one
  parser for bindings and one deny list (FR-004). `--timeout-seconds` changes its
  argparse default from `300` to `None` so an explicit value is detectable; the
  ordinary path substitutes `DEFAULT_TIMEOUT_SECONDS` (300) when it is `None`, so
  ordinary behavior is unchanged.
- **Alternatives**: a separate `secrets session` action (the conflict scenario
  would be vacuous, and the binding grammar would be duplicated); raising
  `MAX_TIMEOUT_SECONDS` (explicit PRD non-goal).

## R2. Lifetime parsing and bounds

- **Decision**: `--lifetime-seconds` is parsed as a string and validated by the
  CLI as ASCII decimal digits, 1 to 43,200; default 28,800. Failures raise
  `SecretBrokerError("lifetime_invalid", ...)`. `--lifetime-seconds` without
  `--session`, or `--session` with `--timeout-seconds`, raises
  `option_conflict`. New constants in `models.py`:
  `DEFAULT_SESSION_SECONDS = 28_800`, `MAX_SESSION_SECONDS = 43_200`.
- **Rationale**: `run` uses `type=int`, which turns `1.5` into an argparse usage
  error with no stable code. A stable reason code makes the refusal testable and
  matches FR-029 of spec 041 (bounded reason codes). The runner re-validates the
  integer (as `run_with_secrets` re-validates `timeout_seconds`) so a non-CLI
  caller cannot bypass the bound.
- **Alternatives**: `type=int` (no stable code; accepts `+5`, ` 5`); duration
  strings like `8h` (extra grammar, PRD says whole seconds).

## R3. Terminal and foreground check

- **Decision**: Before the service is called, the CLI requires all of:
  `sys.stdout.isatty()`; `/dev/tty` opens read-write and `os.isatty` holds on it
  (same probe as `reveal` and `_tty_secret`); and
  `os.tcgetpgrp(tty_fd) == os.getpgrp()` (the broker is the terminal's foreground
  job). Any failure raises `tty_required`. The tty descriptor is closed after the
  check; output goes to standard output.
- **Rationale**: Interrupt and hangup are only meaningful bounds when the broker
  is the foreground job of a live terminal. This one check also excludes CI,
  MCP (no tty), durable jobs (output captured to a file) and remote paths that do
  not allocate a terminal, which is how FR-002 is enforced for those surfaces
  without a list of callers.
- **Alternatives**: stdout check only (a background job with a tty stdout would
  pass and never receive Ctrl-C); a dedicated `foreground_required` code (the
  PRD names `tty_required` for all no-terminal cases; one code is simpler).
- **Residual risk**: an agent harness that allocates a pseudo-terminal passes
  this check. Guidance (FR-023), not the check, keeps agents off session mode;
  this matches spec 041 FR-064 (not an exclusive security boundary).

## R4. Signal ownership and timing

- **Decision**: A `SessionSignals` context manager in
  `sandbox/secrets/session.py`, entered by the CLI immediately after the
  terminal check and before the service is called, installs handlers for
  `SIGINT` (`interrupted`), `SIGHUP` (`hangup`), `SIGTERM` and `SIGQUIT`
  (`interrupted`, decision 1), and sets `SIGTSTP` to ignore. Handlers only
  record the first reason. Previous dispositions are restored on exit. The
  session loop checks the recorded reason before launching the child and on
  every iteration.
- **Rationale**: Installing before the service call means an interrupt during
  the source read cannot raise `KeyboardInterrupt` through `_bounded_call`
  (which catches `Exception`, not `BaseException`) and skip the audit outcome.
  The child already runs in its own session (`start_new_session=True` in
  `run_with_secrets`), so terminal-generated `SIGINT`/`SIGHUP` reach only the
  broker, which then ends the child's group; this gives one place that decides
  the end reason. Ignoring `SIGTSTP` keeps Ctrl-Z from stopping the only process
  that enforces the lifetime (clarification 1). Handlers are installed
  unconditionally, so a `SIGHUP` disposition inherited as ignored (`nohup`) is
  overridden.
- **Alternatives**: catching `KeyboardInterrupt` (no hangup equivalent, races
  with the read); leaving the child in the terminal's process group (child would
  see Ctrl-C directly and could ignore it, and the broker could not attribute the
  reason).

## R5. Lifetime clock

- **Decision**: Record `wall_deadline = time.time() + lifetime` and
  `mono_start = time.monotonic()`. The session expires when either
  `time.time() >= wall_deadline` or `time.monotonic() - mono_start >= lifetime`.
  The selector wait is at most 0.25 s, so expiry is detected within 0.25 s while
  awake and on the first iteration after wake.
- **Rationale**: On macOS `time.monotonic()` does not advance during system
  sleep, so a monotonic-only deadline would let a laptop that slept overnight run
  past the stated end time (clarification 2). Taking the earlier of the two also
  means a wall-clock change can only shorten, never lengthen, the session beyond
  its monotonic bound.
- **Alternatives**: monotonic only (sleep extends the lease); wall only (clock
  set backwards extends the lease).

## R6. Ending the process group within 5 seconds

- **Decision**: On any end reason, `killpg(pgid, SIGTERM)`, then poll
  `killpg(pgid, 0)` and reap the child until the group is empty or 3 s pass, then
  `killpg(pgid, SIGKILL)` and poll up to 1.5 s more. `pgid` is the child's pid
  (new session). For `child_exited` the same sequence runs against any remaining
  group members after the child is reaped. `ProcessLookupError` means done.
- **Rationale**: `run_with_secrets` waits 1 s before `SIGKILL`; a dev server
  benefits from a longer graceful shutdown, and 3 + 1.5 s stays inside the 5 s
  acceptance bound with margin. POSIX does not reuse a pid while it is still a
  process-group id in use, so signalling the group after the leader is reaped is
  safe. Ending on the child's exit (not on output EOF, as `run` does) prevents a
  grandchild that holds the pipe open from keeping the session alive.
- **Alternatives**: `run`'s 1 s grace (abrupt for servers); waiting for EOF on
  `child_exited` (a lingering grandchild would keep the secret alive).

## R7. Live redacted output

- **Decision**: Reuse `StreamingRedactor` with the selected values exactly as
  `run_with_secrets` does. Each `os.read` chunk (at most 64 KiB) is fed to the
  redactor; the returned bytes pass through an incremental UTF-8 decoder
  (`errors="replace"`), the existing `_CONTROL` filter (moved to a shared name in
  `runner.py`), and are written to standard output and flushed. Nothing is
  retained. `redactor.finish()` output is displayed at session end. If `feed`
  raises (`RedactionError` or any other `Exception`), the chunk and anything the
  redactor was holding are dropped, `dropped_chunks` increases by one, and a new
  redactor is created.
- **Rationale**: One redaction path for both modes satisfies spec 041 FR-030 and
  FR-067. `StreamingRedactor` already withholds the trailing token and any
  prefix of a selected value until whitespace disambiguates it; that is the
  fail-closed hold in clarification 4 and is why a newline-terminated line is
  shown at once (SC-006). Reusing `_CONTROL` keeps the terminal-injection
  protection `run` has today. Session display adds its own filter after redaction:
  complete SGR sequences (`ESC [ <digits;> m`) pass, any other escape sequence
  is removed whole (decision 3); `run`'s `_CONTROL` is unchanged.
- **Alternatives**: a time-based flush of held text (could emit a secret prefix);
  reusing `_CONTROL` alone (it removes only the escape byte, leaving `[31m` visible).
- **Implementation note (2026-10-08)**: `redact_text` strips the escape byte
  before matching, so SGR cannot survive if the display filter runs after the
  redactor. The implemented order is: incremental UTF-8 decode,
  `SessionDisplayFilter`, then redaction of the plain projection (SGR taken
  out) by the shared `StreamingRedactor`, with each SGR sequence put back only
  on a span the redactor emitted unchanged. A redacted or discarded span is
  shown without its colour and followed by `ESC [0m`. `StreamingRedactor`
  gained a read-only `consumed_bytes` counter to align output with input; its
  redaction behavior is unchanged. A value split by colour or by a stray
  control character is now redacted in session output.

## R8. Terminal loss

- **Decision**: An `OSError` (including `BrokenPipeError` and `EIO`) while
  writing to standard output ends the session with `hangup` if no reason is
  recorded yet. After any end reason, further display writes are skipped once
  one write has failed.
- **Rationale**: Closing a terminal window can surface as `EIO` on write before
  `SIGHUP` is handled. Python ignores `SIGPIPE`, so a broken pipe arrives as an
  exception, not a signal (clarification 3).

## R9. Service integration and audit

- **Decision**: Add `SecretService.run_session(source, bindings, argv, *,
  lifetime_seconds, signals, display, surface="cli")`. Binding normalization is
  extracted from `run_many` into `_normalize_bindings` and shared. `surface !=
  "cli"` raises `command_denied` before audit intent, as `run_many` does. The
  audit operation is `use_session`. `_operate` gains support for a success
  outcome `reason_code` taken from the callback's result (`reason_code` key), so
  the outcome record carries the end reason with `decision="succeeded"`.
- **Rationale**: `run_many` already validates bindings, destinations and source
  alias before `_operate` writes audit intent, so every argument refusal happens
  before any read (FR-009). The audit schema (`_ALLOWED_FIELDS`) already has
  `reason_code`; no field is added, so FR-026 of spec 041 holds and the
  forbidden-token check is unaffected. A distinct operation name lets an operator
  tell a long-lived recipient from a bounded one in the audit log.
- **Alternatives**: operation `use` with a profile marker (overloads `profile`);
  a new audit field for lifetime (schema change, no PRD need).

## R10. Result shape, elapsed classes, exit status

- **Decision**: New `SessionResult(end_reason, exit_code, elapsed_seconds,
  dropped_chunks, lifetime_seconds)` in `models.py` whose `as_dict()` returns
  `end_reason`, `exit_code`, `elapsed_class`, `dropped_chunks`,
  `lifetime_seconds` and never raw seconds or output. Session elapsed classes:
  `under_1s`, `1_to_10s`, `10_to_60s`, `1_to_10m`, `10_to_60m`, `1_to_4h`,
  `4_to_8h`, `8_to_12h`, `12h_plus`. `RunResult` classes are unchanged. Broker
  exit: `child_exited` maps exactly as `run` (0 stays 0; 1 to 125 pass through;
  anything else, including death by signal, becomes 1, with the same
  `child_failed` message); `lifetime_expired` 0; `interrupted` 130; `hangup` 129
  (decision 2).
- **Rationale**: Keeping `RunResult` untouched avoids changing output that
  existing callers and `test_timeout_terminates_process_group` rely on (FR-019).
  The `run` exit mapping is in `cmd_secrets`; the session reuses it.

## R11. Start and end lines

- **Decision**: The CLI writes one start line and one end line to standard
  output, prefixed `secrets session:`. Start: lifetime in seconds and hours, the
  local end time (ISO 8601 with offset), source alias and key names. End:
  `end_reason`, `exit_code`, `elapsed_class`, `dropped_chunks`. Both pass through
  `redact_structure` like `_emit`.
- **Rationale**: FR-018; the same redaction gate `_emit` uses for every other
  secrets payload.

## R12. Testing approach

- **Decision**: Unit tests in a new `tests/test_secret_session.py` drive
  `run_session` directly with `sys.executable -c` fixtures, lifetimes of 1 to 2
  seconds, a `SessionSignals` whose reason is set from a timer thread, an
  in-memory display sink, and a patched clock for the sleep case. CLI tests in
  `tests/test_secret_commands.py` cover parsing, conflicts, the terminal check
  (patched `isatty`/`open`/`tcgetpgrp`) and exit mapping, following
  `test_run_propagates_trusted_child_failure`. One end-to-end test runs the real
  `sb secrets run --session` under `pty.openpty()` with a temporary
  `SANDBOX_HOME` and fixture source, sends real `SIGINT` and `SIGHUP`, and scans
  the pty transcript, audit file and temp tree for the canary value (pattern:
  `test_isolated_live_cli_flow_never_prints_fixture_value`). The 31-minute run is
  an opt-in evidence run gated by an environment variable and recorded under
  `specs/059-supervised-secret-session/evidence/`.
- **Rationale**: Constitution IV asks for live evidence; this feature does not
  touch a WordPress instance, so the live surface is the real CLI under a real
  terminal.

## Decisions (Fable reviewer, delegated by user, 2026-10-08)

1. Catchable termination requests other than interrupt/hangup (`SIGTERM`,
   `SIGQUIT`) end the session as `interrupted`. Confirmed: a fifth reason buys
   no operator action.
2. Broker exit status: `lifetime_expired` 0, `interrupted` 130, `hangup` 129.
   Confirmed: expiry is the planned end, and ordinary `run` exits 0 on timeout.
3. Revised: complete SGR colour and style sequences pass through; every other
   escape sequence, including an incomplete or malformed SGR, is removed whole.
   `run`'s filter strips only the escape byte, which leaves visible junk.
