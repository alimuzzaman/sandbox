# Contract: `sb host exec` and `--target` for read-only host commands

## Synopsis

```text
sb host exec --remote R (--target P/E | --name SLUG) --service S
             [--project-dir DIR] [--timeout 1..3600] [--json] -- ARGV...
sb host logs|status|diagnose --remote R (--target P/E | --name SLUG) [existing flags]
```

`--name` resolves a stack slug recorded for `R`. `--project-dir` is consulted
only when no stack matches.

## Plain mode

- stdin: the caller's stdin, unchanged, closed at end of input; `/dev/null`
  when stdin is a terminal.
- stdout/stderr: the command's own streams, live.
- Exit status: the command's; 1 refusal; 2 usage; 124 timeout; 255 transport.
- Refusals print `error: <code>: <message>` on stderr.

## `--json` mode

One object on stdout:

```json
{"ok": false, "code": "host_exec_failed", "exit_code": 3,
 "remote": "R", "target": "P/E", "service": "S", "duration_ms": 812,
 "stdout": "...", "stderr": "...",
 "stdout_truncated": false, "stderr_truncated": false}
```

Refusals: `{"ok": false, "code": "<refusal code>", "error": "<message>"}` with
`exit_code` absent. `ok` is true only when `exit_code` is 0.

## Never

- a `-u`, `--user`, `-w`, `-e` or `--privileged` flag on the remote command;
- input bytes in the remote command line, the child's environment, the audit
  file, job records, feedback or the envelope (except the command's own
  output);
- exit 0 for anything but a command that exited 0.
