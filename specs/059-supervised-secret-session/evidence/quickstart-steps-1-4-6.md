# Evidence: quickstart steps 1 to 4 and 6 (T029)

- Date: 2026-10-08, macOS, Python 3.12.8, branch `latest` (spec 059 code).
- Source: a temporary project registering one 0600 fixture source with a
  synthetic `FIXTURE_TOKEN`; a temporary `SANDBOX_HOME`. No real credential.
- Terminal: each session ran `./sb secrets run --session ...` as the session
  leader and foreground job of a real pseudo-terminal (`pty.openpty`,
  `TIOCSCTTY`), driven by a script: Ctrl-C was the byte `0x03` written to the
  terminal, and "closing the terminal" was closing the pty master. This is a
  scripted terminal, not a human at a terminal window.
- The child was `python3 -m http.server 18765 --bind 127.0.0.1`. Port 8765
  from the quickstart was already in use on this machine.
- Only start/end lines, error lines, exit statuses and port checks are
  recorded. None of them contain a value; the final scan confirms it.

## Step 1: automated suites

`python3 -m unittest tests.test_secret_session tests.test_secret_commands
tests.test_secret_service tests.test_secret_mcp`: 107 tests, OK (includes the
pty end-to-end tests for Ctrl-C, `SIGHUP`, terminal close and lifetime expiry).

## Step 2: short lifetime ends the session

    secrets session: started source=fixture keys=FIXTURE_TOKEN lifetime=5s (5s) ends_at=2026-10-08T20:02:30+06:00
    secrets session: ended end_reason=lifetime_expired exit_code=None elapsed=1_to_10s dropped_chunks=0

- A request during the session returned HTTP 200, and the server's log line
  for it appeared live in the terminal before the session ended.
- Exit status 0 (expected 0).
- Port free at the moment `sb` exited (the broker waits for the group to end).

## Step 3a: Ctrl-C

    secrets session: started source=fixture keys=FIXTURE_TOKEN lifetime=28800s (8h) ends_at=2026-10-09T04:02:31+06:00
    ^Csecrets session: ended end_reason=interrupted exit_code=None elapsed=under_1s dropped_chunks=0

(`^C` is the terminal's echo of the interrupt.)

- Exit status 130 (expected 130). Port free at exit.

## Step 3b: terminal closed

- `sb` exited 0.24 s after the terminal closed, status 129 (expected 129).
- Last `use_session` audit outcome: `phase=outcome decision=succeeded
  reason_code=hangup`.
- Port free at exit.

The first run of this step exited 120: Python's final flush of standard
output hit EIO on the closed terminal and replaced the exit status. Fixed in
the CLI (`_detach_lost_terminal`) with a regression test
(`SessionPtyEndToEndTests.test_terminal_closed_exits_129`); the result above is
after the fix.

## Step 4: refusals

| Command variant | Exit | Error |
|-----------------|------|-------|
| stdout redirected to a file | 1 | `tty_required` |
| `--lifetime-seconds 0` | 1 | `lifetime_invalid` |
| `--lifetime-seconds 43201` | 1 | `lifetime_invalid` |
| `--timeout-seconds 60` with `--session` | 1 | `option_conflict` |
| `--destination LD_PRELOAD` (in a pty) | 1 | `destination_denied` |
| `--source nope` (in a pty) | 1 | `source_unknown` |

Audit records added by these six refusals: 0 (no intent, so no read).

## Step 6: ordinary run unchanged

`./sb secrets run --source fixture --key FIXTURE_TOKEN --timeout-seconds 1801 -- true`:
exit 1, `command_invalid: secret use timeout must be between 1 and 1800 seconds`.

## Canary scan

The fixture value was not found in any terminal transcript or in any file
under the temporary root other than the source file itself.
