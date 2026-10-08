# Tasks: Supervised Long-Running Secret Session

**Input**: `specs/059-supervised-secret-session/` (plan.md, spec.md, research.md, data-model.md, contracts/cli.md, quickstart.md)

**Tests**: requested (tests first). Each test task must fail before its implementation task and pass after it.

**Safety for every task**: fixtures use synthetic values only (for example `"TestOnly_" + "Session123456789AbCd"`); never read a real secret source, `.env` file or credential file; never run `sb secrets reveal` or `sb secrets set` against a real source.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1 to US4 from spec.md

## Phase 1: Setup

None. The feature lives in the existing `sandbox/secrets/` package and test layout.

## Phase 2: Foundational (blocks all stories)

- [ ] T001 [P] Write failing tests in `tests/test_secret_session.py` (`SessionModelTests`): `DEFAULT_SESSION_SECONDS == 28_800`, `MAX_SESSION_SECONDS == 43_200`, `END_REASONS == ("lifetime_expired", "interrupted", "hangup", "child_exited")`; `SessionResult.as_dict()` returns exactly `end_reason`, `exit_code`, `elapsed_class`, `dropped_chunks`, `lifetime_seconds` (no `elapsed_seconds`, no `output`); elapsed-class boundaries for 0.5 s, 5 s, 30 s, 5 min, 30 min, 2 h, 6 h, 10 h, 12.5 h; and `RunResult.as_dict()` classes unchanged (`60s_plus` for 1,799 s)
- [ ] T002 Add `DEFAULT_SESSION_SECONDS`, `MAX_SESSION_SECONDS`, `END_REASONS` and the frozen `SessionResult` dataclass to `sandbox/secrets/models.py` to pass T001
- [ ] T003 [P] Write failing tests in `tests/test_secret_service.py`: `SecretService._normalize_bindings` raises the same codes `run_many` raises today (`selection_invalid`, `selection_too_large`, `destination_denied`, `key_invalid`, `source_unknown`) without writing audit intent; `_operate` records `reason_code` on a success outcome when the callback result carries `reason_code`, and still records `None` for existing operations
- [ ] T004 In `sandbox/secrets/service.py`, extract binding validation from `run_many` into `_normalize_bindings` (reused by `run_many`) and let `_operate` pass a success `reason_code` from the result; all existing `tests/test_secret_service.py` and `tests/test_secret_commands.py` cases stay green
- [ ] T005 In `sandbox/secrets/runner.py`, extract the argv and secret-value validation of `run_with_secrets` into a public `validate_child_request(argv, secrets)` and expose the `_CONTROL` filter as `strip_control(text)`; `run_with_secrets` uses both; existing runner tests stay green
- [ ] T006 [P] Write failing tests in `tests/test_secret_session.py` (`SessionSignalsTests`): inside the context `SIGINT` records `interrupted`, `SIGHUP` records `hangup`, `SIGTERM` and `SIGQUIT` record `interrupted`; only the first reason is kept; `SIGTSTP` is ignored while inside; an inherited `SIG_IGN` for `SIGHUP` is overridden; all previous dispositions are restored on exit, including after an exception; `request_end(reason)` records a reason for tests
- [ ] T007 Implement `SessionSignals` in new `sandbox/secrets/session.py` to pass T006 (handlers only record the reason; no I/O in handlers)

## Phase 3: User Story 1 - Run a dev server with a brokered secret for a working session (P1, MVP)

**Goal**: a session child runs with its secrets, shows redacted output live, and ends cleanly when it exits by itself.
**Independent test**: `run_session` with a heartbeat fixture shows lines before the child exits, redacts the value, and returns `child_exited` with the child's status.

- [ ] T008 [P] [US1] Write failing tests in `tests/test_secret_session.py` (`RunSessionTests`) driving `run_session(argv, secrets=..., lifetime_seconds=..., signals=..., display=...)` with `sys.executable -c` fixtures and an in-memory display sink: each binding is present in the child's environment and nothing else beyond the minimal baseline; the parent `os.environ` is unchanged; `Popen` argv equals the given argv; a line written, then a 2 s sleep, reaches the sink before the child exits; the exact value whole and split across two writes is shown only as the redaction marker; control characters are removed; a trailing partial line without whitespace is held until the end and then shown; child exit 0 and 3 return `child_exited` with that `exit_code`; a grandchild still running after the child exits is gone within 5 s; the result holds no output
- [ ] T009 [US1] Implement `run_session` in `sandbox/secrets/session.py` to pass T008: `validate_child_request`, `minimal_environment_values`, `Popen` with `stdin=DEVNULL`, `stdout=PIPE`, `stderr=STDOUT`, `start_new_session=True`, `close_fds=True`; selector loop with at most 0.25 s waits; `StreamingRedactor` feed, incremental UTF-8 decode, `strip_control`, display and flush; end on the child's exit; group termination (`SIGTERM`, 3 s grace, `SIGKILL`, up to 1.5 s); drain and `finish()`; return `SessionResult`
- [ ] T010 [P] [US1] Write failing tests in `tests/test_secret_service.py` for `SecretService.run_session`: audit intent with operation `use_session` is written before `registry.read` (call-order recorder); success outcome has `decision="succeeded"` and `reason_code="child_exited"`; payload is `operation="run_session"` with `source`, `key` or `keys`, `result`, `correlation_id` and no value-derived field; `key_missing` is refused after intent with no `Popen` call and a `refused` outcome
- [ ] T011 [US1] Implement `SecretService.run_session(source, bindings, argv, *, lifetime_seconds, signals, display, surface="cli")` in `sandbox/secrets/service.py` to pass T010, using `_normalize_bindings`, `_operate` and `session.run_session`
- [ ] T012 [P] [US1] Write failing tests in `tests/test_secret_commands.py`: the parser accepts `run --session` and `--lifetime-seconds`; `--timeout-seconds` now defaults to `None` and an ordinary `run` still passes `timeout_seconds=300` to `run_many`; with a patched `_service`, a session prints a start line with `lifetime=28800s (8h)`, an `ends_at` ISO time with offset, source and key names, and an end line with `end_reason`, `exit_code`, `elapsed`, `dropped_chunks`; `child_exited` exit mapping matches `run` (0 returns normally, 11 exits 11 with `child_failed`, -9 and 137 exit 1)
- [ ] T013 [US1] Implement session mode in `sandbox/commands/secrets.py` to pass T012: `--session` and `--lifetime-seconds` flags (string-typed), `--timeout-seconds` default `None` with 300 substituted for ordinary `run`, a display callable writing bytes to `sys.stdout.buffer` and flushing, start and end lines passed through `redact_structure`, and `child_exited` exit mapping shared with `run`

## Phase 4: User Story 2 - The session always ends within its bound (P1)

**Goal**: lifetime expiry, interrupt, hangup and terminal loss each end the process group within 5 s with the right reason, audited.
**Independent test**: short-lifetime and signalled sessions end with the right reason and leave no group member after 5 s.

- [ ] T014 [P] [US2] Write failing tests in `tests/test_secret_session.py`: lifetime 1 s with a fixture that spawns a grandchild and sleeps returns `lifetime_expired` and the group is gone within 5 s of expiry; `request_end("interrupted")` and `request_end("hangup")` from a timer thread return those reasons within 5 s; a reason recorded before launch returns that reason with no `Popen` call; a display sink raising `OSError` (`EIO`) and `BrokenPipeError` returns `hangup`; a child that ignores `SIGTERM` is killed and gone within 5 s; with a patched wall clock that jumps past the deadline while the monotonic clock does not advance, the session returns `lifetime_expired` on the next iteration; a `RedactionError` from the redactor drops that chunk, is counted in `dropped_chunks`, and later output still appears redacted
- [ ] T015 [US2] Extend `run_session` in `sandbox/secrets/session.py` to pass T014: wall and monotonic deadline (expire on the earlier), signal-reason check before launch and on every iteration, display-write failure as `hangup` with later writes skipped, termination escalation shared by every end, redactor replacement on failure with a dropped-chunk count, and runner-level `lifetime_invalid` re-validation (1 to 43,200, int, not bool)
- [ ] T016 [P] [US2] Write failing tests in `tests/test_secret_commands.py` and `tests/test_secret_service.py`: broker exits 0 for `lifetime_expired`, 130 for `interrupted`, 129 for `hangup`; the audit outcome `reason_code` equals the end reason for each of the four reasons
- [ ] T017 [P] [US2] Write a failing end-to-end test in `tests/test_secret_session.py` (`SessionPtyEndToEndTests`): run the real `./sb secrets run --session` under `pty.openpty()` as the foreground job (`os.setsid` plus `TIOCSCTTY` in the child) with a temporary `SANDBOX_HOME`, a temp project registering a 0600 fixture source with a synthetic canary, and a fixture child that prints the canary and a heartbeat and spawns a grandchild; send `SIGINT` and assert exit 130 and the group gone within 5 s; repeat with `SIGHUP` and assert `hangup` in the audit outcome; repeat with `--lifetime-seconds 2` and assert `lifetime_expired`; in every run the canary is absent from the pty transcript, stderr, the audit file and every file under the temporary home (follow `test_isolated_live_cli_flow_never_prints_fixture_value`)
- [ ] T018 [US2] Implement the non-child exit mapping and reason propagation in `sandbox/commands/secrets.py` and make T016 and T017 pass; enter `SessionSignals` in the CLI before calling the service so an interrupt during the source read still yields an audited `interrupted` outcome

## Phase 5: User Story 3 - Unsafe or invalid starts are refused before any secret is read (P2)

**Goal**: every argument, terminal, destination and source refusal happens before any read, with no child.
**Independent test**: each refusal in contracts/cli.md returns its code, with `registry.read`, `Popen` and audit intent never called.

- [ ] T019 [P] [US3] Write failing tests in `tests/test_secret_commands.py`: `lifetime_invalid` for `0`, `43201`, `1.5`, `+5`, ` 5`, `5s`, `abc`; `option_conflict` for `--session` with `--timeout-seconds` and for `--lifetime-seconds` without `--session`; `tty_required` when `sys.stdout.isatty()` is false, when opening `/dev/tty` raises `OSError`, when the opened descriptor is not a tty, and when `os.tcgetpgrp(fd) != os.getpgrp()`; in every case the patched service's `run_session` is never called and the error is `error: <code>: <message>` with exit 1
- [ ] T020 [P] [US3] Write failing tests in `tests/test_secret_service.py`: `run_session` refuses `destination_denied` (`LD_PRELOAD`, `NODE_OPTIONS`), `source_unknown`, `selection_invalid` and `surface="mcp"` (`command_denied`) with no audit intent record, no `registry.read` call and no `Popen` call
- [ ] T021 [P] [US3] Add a guard test in `tests/test_secret_mcp.py`: the MCP secrets tool list stays the four existing tools and `secret_use_profile` exposes no session or lifetime parameter (a regression guard; it passes before and after). Confirm the existing `tests/test_secret_config.py` case refusing `timeoutSeconds` 1801 for use profiles is unchanged
- [ ] T022 [US3] Implement the CLI check order in `sandbox/commands/secrets.py` (lifetime, conflicts, terminal and foreground check reusing the `/dev/tty` probe from `reveal`) and any missing refusal in `run_session` so T019 to T021 pass

## Phase 6: User Story 4 - Guidance keeps agents on bounded use (P3)

**Goal**: skill, operator docs and help mark session mode operator-only.
**Independent test**: the documents state the bound, the end reasons and the risks, and never tell an agent to start a session.

- [ ] T023 [P] [US4] Add an "Operator-only session" subsection to `skills/secret-inspection/SKILL.md` after section 5: agents must not run `--session`; for a long-lived child, ask the human to run it in their own uncaptured terminal; agents keep using bounded `secrets run`; placeholder-only example
- [ ] T024 [P] [US4] Add "5b. Operator session for a long-running child" to `docs/secret-inspection.md`: synopsis from contracts/cli.md, default 8 h and maximum 12 h, the four end reasons and exit codes, terminal and foreground requirement, Ctrl-Z ignored, sleep counts, held partial lines, control characters removed, intentional-recipient warning, uncatchable-kill risk, and the pseudo-terminal residual risk; update "Audit behavior" with `use_session` and "Limitations"
- [ ] T025 [P] [US4] Write a failing test in `tests/test_secret_commands.py` that `run --help` describes `--session` as operator-only and terminal-only and states the 1 to 43200 lifetime range, then update the help strings in `sandbox/commands/secrets.py`
- [ ] T026 [P] [US4] Add an amendment note to `specs/041-safe-secret-inspection/spec.md` (FR-036 and the display clause of FR-037 are amended for session mode by spec 059) and a cross-reference in `specs/041-safe-secret-inspection/contracts/cli.md`; mention session mode in the `README.md` secrets section

## Phase 7: Polish and Cross-Cutting

- [ ] T027 Run `python3 -m unittest tests.test_secret_session tests.test_secret_commands tests.test_secret_service tests.test_secret_mcp tests.test_secret_config tests.test_secret_policy tests.test_architecture_boundaries` and fix regressions
- [ ] T028 [P] Sweep `docs/`, `README.md`, `skills/` and `CLAUDE.md` for statements that `secrets run` can never exceed 30 minutes and qualify them with the session exception
- [ ] T029 Run quickstart.md steps 1 to 4 and 6 in an interactive terminal with a synthetic fixture source; record start/end lines and exit codes (no values) under `specs/059-supervised-secret-session/evidence/`
- [ ] T030 Run quickstart.md step 5 (a session past 30 minutes, then Ctrl-C) once; record the evidence in the same folder and mark feedback 2cfab06f addressed

## Dependencies

- Foundational: T001→T002; T003→T004; T005 after T002; T006→T007. All of Phase 2 blocks the stories.
- US1: T008→T009 (needs T005, T007); T010→T011 (needs T004, T009); T012→T013 (needs T011).
- US2: T014→T015 (needs T009); T016 and T017 need T013 and T015; T018 last in US2.
- US3: T019, T020, T021 need T013; T022 after them.
- US4: independent of code except T025 (needs T013). T026 any time.
- Polish after US1 to US4; T029 then T030 last.

## Parallel examples

- Phase 2: T001, T003, T006 together (different test classes or files); T005 alongside T004.
- US1: T008, T010, T012 together.
- US2: T014 with T016 and T017 once T013 is done.
- US3: T019, T020, T021 together.
- US4: T023, T024, T026 together.

## Implementation strategy

MVP for review = Phase 2 plus US1 and US2 (both P1): a session that runs,
streams and is bounded. Nothing ships to users before US3 and US4 are done:
without US3 the terminal check that keeps CI, jobs and agents out is missing,
and constitution V requires the US4 guidance in the same change as the code.
US1 alone is never shippable, because without US2 a session is not bounded by
interrupt, hangup or lifetime. Evidence runs T029 and T030 come last.

## Task count

| Phase | Tasks |
|-------|-------|
| Foundational | 7 (T001 to T007) |
| US1 | 6 (T008 to T013) |
| US2 | 5 (T014 to T018) |
| US3 | 4 (T019 to T022) |
| US4 | 4 (T023 to T026) |
| Polish | 4 (T027 to T030) |
| Total | 30 |
