# Quickstart: Validating the Supervised Secret Session

Operator-run checks. Use a throwaway fixture source with a synthetic value, never
a real credential. Never run these through an agent tool that captures output.

## Prerequisites

- A project directory with a registered fixture source (see
  `docs/secret-inspection.md`, "Register project sources"), file mode 0600,
  holding one synthetic key such as `FIXTURE_TOKEN`.
- An interactive terminal.

## 1. Automated suites

```bash
python3 -m unittest tests.test_secret_session tests.test_secret_commands \
  tests.test_secret_service tests.test_secret_mcp
```

Expected: all pass, including the pty end-to-end test (real `SIGINT` and
`SIGHUP`, canary absent from the transcript, audit and temp tree).

## 2. Short lifetime ends the session

```bash
./sb secrets run --session --lifetime-seconds 5 --source fixture \
  --key FIXTURE_TOKEN --project-dir . -- python3 -m http.server 8765
```

Expected: start line with `lifetime=5s` and an `ends_at`; server log lines appear
live; after about 5 s the end line shows `end_reason=lifetime_expired`; exit 0;
`lsof -i :8765` shows nothing within 5 s.

## 3. Ctrl-C and closing the terminal

Start the same command with the default lifetime. Press Ctrl-C: end line shows
`end_reason=interrupted`, exit 130, port free within 5 s. Start again and close
the terminal window: from another terminal, the port is free within 5 s and the
audit log's last outcome for `use_session` has `reason_code=hangup`.

## 4. Refusals

```bash
./sb secrets run --session --source fixture --key FIXTURE_TOKEN -- true > /tmp/out.txt   # tty_required
./sb secrets run --session --lifetime-seconds 0 --source fixture --key FIXTURE_TOKEN -- true      # lifetime_invalid
./sb secrets run --session --lifetime-seconds 43201 --source fixture --key FIXTURE_TOKEN -- true  # lifetime_invalid
./sb secrets run --session --timeout-seconds 60 --source fixture --key FIXTURE_TOKEN -- true      # option_conflict
./sb secrets run --session --source fixture --key FIXTURE_TOKEN --destination LD_PRELOAD -- true  # destination_denied
./sb secrets run --session --source nope --key FIXTURE_TOKEN -- true                               # source_unknown
```

Expected: each exits 1 with the code shown and no audit intent record (the
source was not read).

## 5. Past 30 minutes (evidence run, once)

Run step 2 with the default lifetime and leave it 31 minutes. Confirm the server
still answers, then press Ctrl-C. Record the start and end lines (they contain no
value) under `specs/059-supervised-secret-session/evidence/`.

## 6. Ordinary run unchanged

```bash
./sb secrets run --source fixture --key FIXTURE_TOKEN --timeout-seconds 1801 -- true   # still refused
```
