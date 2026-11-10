# Implementation Plan: Host Exec

**Branch**: `feat/host-exec` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/074-host-exec/spec.md`

## Summary

Add `sb host exec`, which runs one argv inside a running service of a hosted
target this controller recorded, with the controller's stdin passed straight
to the container. The remote side is `docker compose exec -T` built from the
same `_compose_prefix` that `host apply` and `host logs` use, wrapped in GNU
`timeout`. The transport is the existing SSH argv builder
(`remote.ssh_command_args`) run as one child whose stdin is the controller's
own stdin descriptor, so the bytes go kernel to `ssh` and never pass through
Python strings, argv, environment or logs. The same target resolver lets
`host logs|status|diagnose` take `--target`/`--name` and read a stack's
rendered manifest instead of the current directory's template (feedback
`da5d522e`).

## Technical Context

**Language/Version**: Python 3.12 (controller CLI), POSIX `sh` plus GNU
coreutils `timeout` on the remote

**Primary Dependencies**: standard library only; system `ssh`; Docker Compose
v2 on the remote

**Storage**: controller files under `$SANDBOX_HOME/runtime/`: `hosts.json`
(read), `stacks/registry.json` (read), new `host-exec/audit.jsonl` (append)

**Testing**: `unittest` modules under `tests/`, run by Haiku helpers on the
`xspeed-dev` remote through `job-start`

**Target Platform**: macOS/Linux controller; Linux remote

**Project Type**: CLI

**Performance Goals**: three SSH round trips per exec (resolve home, list
services, exec); no added latency on the data path

**Constraints**: stdin never buffered or copied by Sandbox; `--json` captures
at most 1 MiB per stream; timeout 1 to 3600 s, default 300 s

**Scale/Scope**: one target per call; a few hundred lines of new code plus
tests and docs

## Constitution Check

| Principle | Status |
|---|---|
| I. Per-project instance model | Not affected: hosted targets, not local instances. |
| II. Registry as source of truth | Reads `hosts.json` and the stack registry through their modules; no direct writes. |
| III. Single entry file, modular package | New logic in `sandbox/hosting/` and `sandbox/commands/host_exec.py`; `sb` untouched. |
| IV. Live-stack verification | Live check on `xspeed-dev` target `xspeed-hub-test-stacks/sandbox` after tests pass. |
| V. Idempotency, docs with code | Exec has no Sandbox-side state beyond an append-only audit; docs and both skill copies change in the same commit. |
| VI. Parity before removal | Nothing removed. The `stack_template_requires_stack_up` refusal stays for template use without `--target`. |
| Module boundaries (CLAUDE.md) | Registers no new top-level command; no consumers of `sandbox_core.py`, `COMMANDS` or MCP helpers; registry JSON is read only through `stack_registry.read()` and `hosting.load_host_state()`. |

Gate: PASS. Re-checked after Phase 1: PASS.

## Project Structure

### Documentation (this feature)

```text
specs/074-host-exec/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/host-exec-cli.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/target_resolution.py   # new: --target/--name -> ResolvedTarget
sandbox/hosting/host_exec.py           # new: types, remote command, busy check, runner, audit
sandbox/commands/host_exec.py          # new: CLI handler and `--` argv split
sandbox/commands/hosting.py            # dispatch `exec`; --target/--name for logs/status/diagnose
sandbox/cli.py                         # host subparser: `exec` choice, help text; argv split call
tests/test_host_exec.py                # new
tests/test_host_target_resolution.py   # new
docs/remote-hosting.md, docs/sandbox-config-reference.md
skills/sandbox-cli/SKILL.md, .agents/skills/sandbox-cli/SKILL.md
```

**Structure Decision**: pure-ish hosting logic in `sandbox/hosting/` (no CLI
parsing, testable with fakes), CLI glue in `sandbox/commands/host_exec.py`,
and only dispatch lines in the large shared `hosting.py` and `cli.py`.

## Design

1. **Argv split.** `argparse.REMAINDER` on the shared `host` parser swallows
   every option, so `main()` calls `host_exec.split_passthrough(argv)` before
   parsing: when the top-level command is `host` and a `--` is present, the
   tail is set aside. If the parsed action is `exec`, the tail becomes
   `args.exec_argv`; for any other action the original argv is parsed again,
   so other commands behave exactly as before.
2. **Resolution** (`target_resolution.resolve`). `--target P/E` (via
   `destroy.parse_target`) or `--name SLUG` with `--remote R`:
   - a stack record for `R` with that project/environment (or slug) wins; its
     project dir is `runtime/stacks/<slug>/`, checked to be exactly
     `stack_registry.stack_dir(slug)`;
   - otherwise the manifest in `--project-dir` (default `.`) is validated for
     `E`; a template there, or a different project, is `host_target_unknown`;
   - the hosting record `R/P/E` must exist in `hosts.json`, else
     `host_target_unknown`;
   - `--environment` that disagrees with `--target` is `host_target_invalid`.
3. **Busy rule** (refuse, never wait): `host_target_busy` when the stack's
   last apply job is in an active lifecycle, when the hosting record has
   `active_operation`, `recovery_uncertainty` or an active image activation,
   or when the per-target effect lock (`RecoveryRepository.effect_lock`) cannot
   be taken within 0 s. The lock is released before the run.
4. **Service check**: `config --services` with `--profile '*'` through
   `_remote_checked`; a service not listed is `host_exec_unknown_service`
   with the list.
5. **Remote command**: `timeout --kill-after=10 N env <compose prefix>
   --profile '*' exec -T <service> <argv...>`, every token shell-quoted. No
   `-u`, `-w`, `-e` or `--privileged`. `env` keeps the prefix's
   `SANDBOX_HOST_ENV_FILE=...` assignment valid after `timeout`.
6. **Runner**: `subprocess.Popen(ssh_command_args(entry, cmd, multiplex=False),
   stdin=<controller stdin fd, or DEVNULL for a TTY/closed stdin>)`. Plain
   mode inherits stdout/stderr; `--json` drains both pipes in threads into
   1 MiB buffers. A local watchdog at `N + 40` s kills `ssh` and reports
   `host_exec_watchdog_expired` (exit 255, remote state unconfirmed). Exit
   124/137 after at least `N` s is labelled `host_exec_timeout` with the status
   passed through; 255 or an `ssh` that cannot start is
   `host_exec_transport_failed`.
7. **Exit status**: the remote status unchanged; refusals exit 1, usage
   errors 2, transport and watchdog 255. `ok` is true only for exit 0.
8. **Audit**: one JSON line in `runtime/host-exec/audit.jsonl` (dir 0700,
   file 0600, `flock`, `O_APPEND`) with `at`, `remote`, `target`, `service`,
   redacted `argv`, `exit_code`, `code`, `duration_ms`. Written for every run
   that reached the exec step.
9. **Read-only commands**: in `cmd_host`, `logs|status|diagnose` with
   `--target` or `--name` resolve first and replace `project_dir` and
   `environment`; every later line is unchanged.

## Complexity Tracking

No constitution violations.
