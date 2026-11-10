# Research: Host Exec

## R1. Transport for stdin

- **Decision**: build the SSH argv with the existing `remote.ssh_command_args`
  and run it as one `Popen` child whose stdin is the controller's own stdin
  descriptor (or `/dev/null` when stdin is a terminal or closed).
- **Rationale**: `sb exec --remote` and `job-start` both force
  `stdin=DEVNULL` (`transports/remote_jobs.py` via `ssh_process`;
  `jobs/supervisor.py`), so neither can carry input. `ssh_process(input_data=)`
  would buffer the whole input in Python and capture all output, which loses
  streaming. Handing the descriptor to `ssh` streams the bytes without Sandbox
  ever reading them, which also makes "never logged" structural.
- **Alternatives**: `ssh_process(input_data=...)` (buffers, no live output);
  durable jobs (no stdin, and job records would be the wrong place for a
  secret); a new transport (forbidden by the brief).

## R2. Multiplexing

- **Decision**: `multiplex=False`.
- **Rationale**: `ssh_stream` documents that a dropped shared ControlMaster
  makes every session on it exit 255 even when the remote command finished.
  An exec can run for up to an hour; its own connection avoids a false 255.

## R3. Timeout

- **Decision**: remote `timeout --kill-after=10 N`, plus a local watchdog at
  `N + 40` s that kills `ssh` and reports `host_exec_watchdog_expired`; the
  remote status (124, or 137 after the kill) is passed through, never
  rewritten (review finding).
- **Rationale**: the remote wrapper ends the Compose exec client; the local
  deadline covers a hung connection. Docker does not signal a process started
  by `exec` when the client goes away, so a command ignoring end of input can
  outlive the timeout inside the container (documented limit).

## R4. Busy rule

- **Decision**: refuse with `host_target_busy`; check stack job lifecycle,
  hosting record fences and a 0-second try of the per-target effect lock.
- **Rationale**: apply holds the effect lock (`target_mutation_transaction`)
  for its whole run; stack applies run as local durable jobs whose lifecycle
  `host_stack._refuse_if_active` already reads. Waiting could hold a terminal
  for an entire apply.

## R5. Resolving plain targets

- **Decision**: plain targets resolve through `--project-dir` (default `.`).
- **Rationale**: `hosts.json` records hold no manifest location
  (`hosting.py` apply writer), and the compose file list is needed to build
  the same prefix apply uses. Stacks carry their manifest path in the stack
  registry.

## R6. Argv after `--`

- **Decision**: split the tail off before `argparse` when the command is
  `host`, and re-parse the original argv for every other host action.
- **Rationale**: adding `nargs=REMAINDER` to the shared `host` parser makes it
  swallow `--remote` and every other option (checked with Python 3.12).
