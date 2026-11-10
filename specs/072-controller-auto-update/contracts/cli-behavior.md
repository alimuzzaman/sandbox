# Contract: CLI behavior

## Entry

`sandbox.cli.main` calls `sandbox.controller_update.hook.maybe_auto_update(args, argv)`
immediately after `parse_args` and before `maybe_auto_migrate`, the feedback
early return and the outer-remote forward. The call either returns (the command
continues in this process) or replaces the process (`execve`) or exits 1
(`Stranded`). It never raises: any unexpected exception is swallowed and the
command continues on the current code.

## Gating order (first match wins, all silent)

1. `SANDBOX_AUTO_UPDATE_REEXEC` set (any value) -> no action.
2. `SANDBOX_DURABLE_JOB_ID` set -> no action.
3. Machine output (`args.json` true, or `args.format` in `json, jsonl`) -> no action.
4. Process is not the real `sb` entry (`sys.argv[0]` resolves to `ENTRY`) -> no action.
5. `SANDBOX_AUTO_UPDATE` in `0,false,no,off` (case-insensitive) -> no action.
6. Command is not remote-capable -> no action.
7. No hint in the state file differs from `HEAD`; or the checkout is not a git
   repo -> no action.
8. `SANDBOX_AUTO_UPDATE` unset or unrecognised and
   `defaults.controller_auto_update` is false -> no action.

Remote-capable: `args.cmd` in `remote, deploy, host, preview, hermes`; or a
non-empty string `args.remote`; or `args.cmd` in `test, e2e, ci, job, jobs`
without `args.local`.

## Notices (stderr, one line each, prefix `sb: `)

| Reason | Text |
|---|---|
| updated | `sb: controller updated {old7} -> {new7} from {upstream}; re-running the command.` |
| detached | `sb: controller not updated: remote {remote} runs {new7}, but this checkout is pinned (detached at {old7}).` |
| no_upstream | `sb: controller not updated: remote {remote} runs {new7}, but branch {branch} follows no published branch.` |
| origin_not_github | `sb: controller not updated: origin is not a GitHub repository.` |
| dirty | `sb: controller not updated: remote {remote} runs {new7}, but {root} has uncommitted changes.` |
| locked | `sb: controller not updated: another Sandbox process is updating {root}.` |
| fetch_failed | `sb: controller not updated: could not fetch {upstream} from origin ({detail}).` |
| not_on_upstream | `sb: controller not updated: remote {remote} runs {new7}, which is not on {upstream}.` |
| not_descendant | `sb: controller not updated: {new7} is not a fast-forward of {old7}.` |
| ff_failed | `sb: controller not updated: fast-forward to {new7} failed ({detail}).` |
| mcp_requirements | `sb: MCP server dependencies changed; run ./sb mcp-install and restart your MCP client.` |
| refresh_failed (rolled back) | `sb: controller update to {new7} rolled back: setup refresh failed ({detail}); running on {old7}.` |
| rollback_failed | `sb: controller update to {new7} failed and could not be rolled back; {root} is at {head7}. Recover with: git -C {root} status` |

`detached`, `no_upstream` and `dirty` print only when the hint commit exists
locally and `HEAD` is its strict ancestor; otherwise they are silent.
`detail` is at most 120 characters of git's first stderr line with control
characters removed.

## Re-exec

`os.execve(sys.executable, [sys.executable, str(ENTRY), *sys.argv[1:]], {**os.environ, "SANDBOX_AUTO_UPDATE_REEXEC": "1"})`.
The same interpreter is kept (normally `.cli-venv/bin/python`, which the
refresh just converged), so the re-run does not repeat interpreter discovery.
The update lock is released before exec. The re-run's exit status is the
command's exit status.

## Exit codes

The hook adds one: `1` after `Stranded`. Every other outcome leaves the
command's own exit status unchanged.

## Opt-out

| Control | Values | Precedence |
|---|---|---|
| `SANDBOX_AUTO_UPDATE` | `0,false,no,off` off; `1,true,yes,on` on | wins |
| `defaults.controller_auto_update` in `$SANDBOX_HOME/sandbox.local.yml` (or `sandbox.yml`) | YAML bool | used when the env var is unset or unrecognised |

Default: on.
