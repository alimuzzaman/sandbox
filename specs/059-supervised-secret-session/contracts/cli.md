# CLI Contract: `sb secrets run --session`

Amends `specs/041-safe-secret-inspection/contracts/cli.md` ("Use without
seeing") for session mode only. Ordinary `secrets run` is unchanged.

## Synopsis

```text
sb secrets run --session [--lifetime-seconds N]
               --source ALIAS (--key KEY [--destination NAME] | --secret KEY=DEST ...)
               [--project-dir DIR] -- ARGV...
```

- `--session`: opt into session mode. Operator-run, local, foreground only.
- `--lifetime-seconds N`: whole seconds, 1 to 43200. Default 28800 (8 hours).
- `--timeout-seconds` is not accepted with `--session`.
- `--key`/`--destination`/`--secret` and `-- ARGV` behave exactly as in ordinary
  `run` (same deny list, same uniqueness rules, direct exec, no shell).
- There is no `--json`: the result is shown as the end line only.

## Refusals (all before any secret value is read, no child started)

| Condition | Code |
|-----------|------|
| `--lifetime-seconds` not decimal digits, below 1 or above 43200 | `lifetime_invalid` |
| `--session` with `--timeout-seconds`, or `--lifetime-seconds` without `--session` | `option_conflict` |
| stdout not a tty, `/dev/tty` cannot be opened as a tty, or broker not the terminal's foreground job | `tty_required` |
| the command itself (`ARGV[0]`, by basename) is `sudo`, `sudoedit`, `doas`, `su`, `pkexec` or `run0` | `escalation_unsupported` |
| binding malformed, duplicate key or destination | `selection_invalid` / `destination_denied` |
| destination on the deny list | `destination_denied` |
| unknown source alias | `source_unknown` |
| non-CLI surface | `command_denied` |
| audit intent cannot be recorded | `audit_unavailable` / `audit_invalid` |

After audit intent and the source read:

| Condition | Code |
|-----------|------|
| key not in source | `key_missing` (no child started) |
| source unsafe or unparsable | existing spec 041 codes |
| child cannot be started | `command_invalid` |

Refusals print `error: <code>: <message>` to stderr and exit 1.

## Terminal output

Start line (after the child is launched):

```text
secrets session: started source=ALIAS keys=KEY[,KEY] lifetime=28800s (8h) ends_at=2026-10-08T18:30:00+06:00
```

Then the child's combined stdout and stderr, redacted, with complete SGR colour
sequences kept and every other control sequence removed whole, written as it arrives. Text after the last whitespace may be held until
the line completes or the session ends.

End line:

```text
secrets session: ended end_reason=child_exited exit_code=0 elapsed=1_to_4h dropped_chunks=0
```

`end_reason` is one of `lifetime_expired`, `interrupted`, `hangup`,
`child_exited`. `exit_code` is the child's status for `child_exited`, else
`None`.

Warning line, only when the group did not end within the bound
(`group_ended=false` in the audited result; exit status is unchanged):

```text
secrets session: warning: a process in the child's group could not be ended within the bound (did the child escalate privileges?); it may still hold the secret
```

## Exit status

| End | Exit |
|-----|------|
| `child_exited` with 0 | 0 |
| `child_exited` with 1 to 125 | same, plus `error: child_failed: secret use command failed` on stderr |
| `child_exited` otherwise | 1, same message |
| `lifetime_expired` | 0 |
| `interrupted` | 130 |
| `hangup` | 129 |

## Signals

| Signal to broker | Effect |
|------------------|--------|
| `SIGINT` (Ctrl-C) | end `interrupted` |
| `SIGHUP` (terminal closed) | end `hangup` |
| `SIGTERM`, `SIGQUIT`, `SIGUSR1`, `SIGUSR2`, `SIGALRM`, `SIGVTALRM`, `SIGPROF`, `SIGXCPU` (every other catchable signal whose default would end the broker) | end `interrupted` |
| `SIGTSTP` (Ctrl-Z) | ignored |
| `SIGPIPE`, `SIGXFSZ` | left ignored by Python; a failed terminal write surfaces as `OSError` → `hangup` |
| `SIGKILL`, `SIGSTOP` | cannot be handled; child outlives the broker (accepted risk) |

On every end the child's process group gets `SIGTERM`, then `SIGKILL` for
anything left after 3 s; the group is gone within 5 s.

Limit: a group member running with other privileges than the broker (the
child ran `sudo`, `doas`, `su`, `pkexec` or `run0` somewhere inside) cannot be
signalled by the broker. `sudo` relays `SIGTERM` to its command, which usually
ends it, but nothing can force it. The direct form is refused up front
(`escalation_unsupported`); escalation deeper inside the child is detected
after the bound (the broker's `killpg` is refused with `EPERM` once only
privileged members remain) and reported as the warning line with
`group_ended=false`, never as a clean end.

## Audit

Operation `use_session`; intent before the read; outcome with
`decision=succeeded` and `reason_code=<end_reason>`, or the refusal code. No new
audit fields.

## Not exposed

No MCP tool, no use-profile field, no durable-job or remote form. The MCP tool
list stays `secret_source_info`, `secret_inspect`, `secret_validate`,
`secret_use_profile`.
