# Per-person tokens for the remote control service: handoff

**Status: PAUSED by the owner on 2026-11-09. Do not resume without the owner saying so.**

This note stands alone. A reader with no other context can resume from it.

**Scope may change on resume.** The owner is moving this server's control connection to Tailscale first, so the scope may narrow or change. Confirm the scope with the owner before doing anything else. See [What Tailscale may change](#what-tailscale-may-change).

## Summary of state

- Branch: `feat/per-person-tokens`, cut from `f48efad` (equal to `origin/latest` and `origin/main` when checked).
- Spec: `specs/071-per-person-remote-tokens/`. Only `prd.md` exists, and it is **`NOT READY`**.
- **No implementation exists.** No code, tests or feature docs have been written. This note and the PRD are the whole of the work so far.
- No tests were written or run for this work, because there is nothing to test yet.
- No server was changed or contacted. No live proof was run. No secret or env file was opened.

## The owner's decisions

Verbatim, settled, not to be re-asked unless the owner reopens them:

> "create Per-person tokens, anyone with ssh access can create it. they will not have ssh access/passthrough."

> Asked what a token may do, the owner chose **everything except SSH**, after being told that `job_start` and `instance_exec` stay open and every token holder could likely reach root on the server. Implement that choice and record it plainly in the docs and in your security-review list. Do not narrow it on your own.

Also from the owner's brief: a person's name is a free-text label with no email list. HTTPS source upload is not to be built ("the owner didn't choose it").

## The system today

`_run_streamable_http` in `mcp/wp-server/server.py:843-976` has a single bearer token, from `SANDBOX_REMOTE_MCP_TOKEN` or `--token`, checked in `_BearerAuthMiddleware` (`server.py:948-952`) with a plain `!=`. The middleware covers the MCP app and the `/diagnostics`, `/resources`, `/inventory` and `/wp-cli` routes. The service runs as `alim` under systemd (a user unit, `sandbox/core/_remote.py:2692-2731`), bound to 127.0.0.1:9174, and is published through nginx at `https://sandbox.xspeedcdn.online` on remote `xspeed-dev`. Controllers keep `bearer_token` in the remote registry (`sandbox/core/_remote.py`, `sandbox/commands/remote.py`).

## Required behaviour (from the owner's brief)

1. **Create, list, revoke.** Anyone with SSH access to the server can create a token with an `sb` command. It must work both when run on the server and from a controller over SSH. A person's name is a free-text label, with no email list. The raw token is shown exactly once.
2. **Storage.** The server keeps only a hash of each token, in a mode-0600 file under Sandbox's own home, with label, created time, optional expiry and revoked state. Never write the raw token to disk, state or logs. Never touch the `personal` secrets file: a write during anyone's apply fails that apply.
3. **Check.** Compare in constant time on every route the middleware covers today. Revocation and new tokens take effect without restarting the service. Expired and revoked tokens get the same `401` as an unknown one.
4. **No SSH or passthrough for token holders.** Their token must never yield an SSH session, tunnel, host passthrough or the SSH credentials: `remote_ssh` is operator-only today. Trace every tool and route to confirm no path hands out SSH access or the operator key, for example through a secret tool or a returned file. Report anything that does. Everything else stays available, per the owner's decision.
5. **Attribution.** Log which label made each request, with no token value. Jobs and actions should record that label where it's cheap to do.
6. **Existing bearer token keeps working.** Controllers and the two live remotes, `xspeed-dev` and `xcloud-london`, must not break. An old service with no token file behaves as today.
7. **How people connect.** Document how a token holder configures their MCP client with `https://sandbox.xspeedcdn.online` and the token.

**Known limit to establish and report.** Source upload for `deploy` and some job flows goes over SSH (`deploy_transport` is still listed as not service-backed in `mcp/wp-server/server.py`, around line 580). Do not build an HTTPS upload. List exactly which `sb` and MCP flows will not work without SSH, so the owner knows what token holders can really do.

## Process requirements

- This is a material security feature, so it goes through spec-kit, in the order `AGENTS.md` gives: `speckit-refine`, independent readiness review, `speckit-specify`, `speckit-clarify`, `speckit-plan`, `speckit-tasks`, `speckit-analyze`, then implementation. Never hand-author `specs/<n>/` files.
- **Test first.** The owner said so, and `AGENTS.md` allows a user instruction to override its test-after default.
- Update the docs with the behaviour.
- **Stop for the owner's security review.** This is an authentication change on a service reachable from the public internet. It stops for review before any merge, PR or server update. Do not open a PR without asking.

## Hard boundaries

- `main` and `latest` are not to be touched: no push, merge or PR into either.
- Push only this branch, with `git push -u origin feat/per-person-tokens`, one named ref, and first check its upstream is its own name. Never `--tags`, `--all`, `--mirror`, `--prune`. No force push. Never `git tag`. Never trigger a GitHub Actions workflow.
- Do not change anything on any server. No `sb remote service migrate`, `sb remote provision`, `sb host apply` or `destroy`. Read-only inspection only when needed. State plainly in the handback that no live proof was run.
- Never open, print or search a secret or env file, including `~/.sandbox/mcp-remote.env` on the server. Never run `secrets reveal`.
- Do not change `hosting/local_images.py:164`, `core/_hosting.py:815-820` or `commands/ui_dash.py`; they await separate human security review.
- Other worktrees are active. Avoid `fix/hosting-medium-findings` areas (`sandbox/hosting/destroy.py`, `zone_coverage/`, `jobs/registry.py`, `application/job_service.py`) and `feat/autologin-token-gate` areas (autologin in `sandbox/commands/hosting.py`, `sandbox/core/_hosting.py`).
- Forbidden git: `git stash`, `git reset`, `git checkout -- .`.
- Scratch goes in `/Users/alim/Sites/git/.scratch/sandbox/per-person-tokens/`, never in the worktree.
- Commits, PRs and changelog entries must not mention the tools or assistants used to write them. No emojis. Do not use `--no-verify`.

## Helpers and tests

- Follow the owner's user-level delegation rules: only the small helper models they name, an explicit model on every spawn, and every brief passes the rules on. Each helper gets one bounded task and is never reused. Stay under 30 helpers for this task; 4 have been used.
- Tests and type-checks run in a small helper, which reports the command, pass/fail/skip counts, the first failure's excerpt (about 40 lines at most) and the log path. Name specific test files; whole-directory runs can hang on network behaviour.

## Required handback when the work finishes

Spec path; the commands; files changed; checks actually run with counts; new failures against `origin/latest`; which flows need SSH; every path found from a token to host access, ranked; what was not verified; and the security-review items.

## Where the process stands

| Step | State |
|---|---|
| `speckit-refine` (`prd.md`) | **In progress.** Drafted, then reviewed. Review findings not yet applied. |
| Independent readiness review | Run once. **Verdict: REOPEN.** Must be re-run after the PRD is updated. |
| `speckit-specify` | Not started. **Blocked** on the review and on the scope decision. |
| `speckit-clarify`, `speckit-plan`, `speckit-tasks`, `speckit-analyze` | Not started. |
| Tests, implementation, docs | Not started. |
| Owner security review | Not reached. |

Spec-kit artifacts that exist: `specs/071-per-person-remote-tokens/prd.md` only. `.specify/feature.json` points at spec 071. There is no `spec.md`, `plan.md`, `tasks.md` or checklist.

**Next step:** confirm scope with the owner after the Tailscale move, settle the three questions below, apply the review's fixes to `prd.md` through `speckit-refine`, then run a new independent readiness review.

## Readiness review findings (not yet applied)

Verdict: REOPEN, because findings 1 to 3 below need owner input.

Needing an owner decision:

1. **Who may list and revoke**, and what happens when the SSH user differs from the service account. The owner said "anyone with ssh access" for creating; list and revoke are not stated. The service is a systemd user unit under the service account's home.
2. **Whether per-person tokens cover `/diagnostics`**, including process details. The brief says every route the middleware covers, so this looks already answered, but the PRD should say so explicitly.
3. **Behaviour when the token store becomes unavailable after startup, and when a create or revoke races with authentication** or an in-flight request. Scenario 9 covers part of the first; the race is not covered.

Wording and acceptance fixes:

- Define observable boundaries for "the raw token appears once" and "no file contains a raw token" (stdout, stderr, service logs, and so on).
- Make request and job attribution measurable: one service log event per request, plus what a person sees when job attribution is unavailable.
- Clarify unsafe-store handling, and require an operator-visible warning with no secret in it. The PRD lists fail-closed for per-person tokens with the shared token unaffected as an assumption for security review.
- State no-token-store behaviour as a compatibility outcome to preserve and verify.
- Add a negative scenario for a valid per-person token presented to a service that has not been updated.
- Remove implementation mechanisms (constant-time comparison, scanning all entries, mode `0600`, store location) from the PRD and keep the outcomes. Those mechanisms stay owner requirements and belong in the specification.
- Remove repository file-ownership detail from the PRD.

## Paths from a token to host access (findings so far)

From a read-only code trace, marked PARTIAL. No command was executed against anything. Item 1 and the catalog facts were confirmed directly from the code; the rest are from the trace and have not been independently confirmed.

**Tools exposed.** The service unit sets no `SANDBOX_MCP_GROUPS`, so `DEFAULT_MCP_GROUPS` applies (`mcp/wp-server/tools/manifest.py:17-19`) unless the env file sets it. The installer writes that env file with only the token line (`sandbox/core/_remote.py:5016`), so only a hand edit could change the catalog. The live file was not opened and must not be. Default groups: `instances`, `domains`, `runtime`, `jobs`, `wp`, `net`, `data`, `fs`, `context`, `resources`, `feedback`, `sync`, `owned_storage`, `delivery`. Not loaded by default: `mail`, `cache`, `abilities`, `skills`, `debug`, `e2e`, `ci`, `asyncjobs`, `secrets`, `plugin_check`, `remote`, `hermes`, `recovery`. No tool named `remote_ssh` exists in the manifest.

Key to what each path yields: (a) SSH session or tunnel; (b) host code execution; (c) SSH key read or `authorized_keys` write; (d) operator token, env file or other remotes' registry entries; (e) use of the server's stored SSH credentials against other remotes.

| Rank | Path | Yields | Execution | Certainty |
|---|---|---|---|---|
| 1 | `job_start`, `job_matrix`, `job_retry`, with `job_output` and artifact tools | (b); (a), (c), (d) by what the commands could do | Explicit argv as a host job (`tools/jobs.py:72-90`: "a general host-job primitive"); output returned | Host job capability proved from code; onward effects reasoned, not tested |
| 2 | `instance_exec` | (b); possibly (c), (d) | Argv in a declared service or container; `sh -lc` allowed | Execution proved; reach beyond the container not established |
| 3 | `ensure_instance`, `apply_config` | (b); possibly (c), (d) | Caller-chosen `project_dir`; config may control mounts or Compose options | Likely; not proved. The likeliest route from container to host |
| 4 | `wp_exec`, `wp_cli`, `run_tests`, `wp_cli_async` | (b) | Container or CLI, on the selected instance | Execution proved; host reach not established |
| 5 | `resource_cleanup_apply`, `/resources` | (b), limited | Fixed contract (`server.py:102`), no arbitrary argv | Proved bounded |
| 6 | `/wp-cli` | (b), limited | Fixed `sb wp --local` on a deployed project (`server.py:355`) | Proved bounded |
| 7 | `remote_deploy` | (e) if the `remote` group is enabled | Registered SSH credentials | Group not loaded by default |
| 8 | `fs_read`, `fs_write`, `fs_list`, `tail_log` | (c), (d) only if a path escapes the instance | Instance filesystem APIs | Symlink containment not established |
| 9 | `/diagnostics?processes=1` | Process and container metadata only | Fixed `ps` and `docker stats` (`server.py:59`) | Proved |

**Rank 1 needs saying plainly at security review.** A host job running as the service user could, by reasoning from what it is, read `~/.ssh` and the service env file (the shared bearer), add a key to `authorized_keys`, run an `ssh` client, and write the token store to mint new tokens. Read strictly, that conflicts with requirement 4, "must never yield". The owner chose to keep `job_start` open knowingly, so this is a report item and not something to fix without the owner. "No SSH" holds for purpose-built features only, not against a determined holder.

Found to give none of the above in the trace: `/diagnostics` without a query, `/inventory`, the `domains`, `net`, `data`, `context`, `feedback`, `sync`, `owned_storage` and `delivery` groups, `instance_status`, `instance_logs`, `native_*`, `wp_rest`.

Not traced:

- Symlink behaviour and canonical containment for the `fs` tools.
- Whether `project_dir` can resolve outside intended workspaces, and how Compose mounts are filtered.
- Whether the service user has sudo or docker-group access, which decides root.
- Subprocess environment and redaction end to end.
- Non-default tool groups, if the live catalog enables any.

## Flows that need SSH (known so far)

A token holder works through an MCP client against the service, so MCP tools run on the server itself. The limit is that source cannot reach the server without SSH: a holder can only work with projects and workspaces already there.

- **`sb` is not usable by a token holder as things stand.** `sb remote add` requires an SSH URL (`sandbox/commands/remote.py:989-991`), and only `provision` writes `control_url` and `bearer_token` (`remote.py:1742-1759`), after an SSH bootstrap.
- **SSH only, in the `sb` CLI:**
  - Remote jobs through `RemoteJobTransport` (`sandbox/transports/remote_jobs.py:563-573`): `job-start`, `job-status`, `job-output`, `job-list`, `job-cancel`, `job-retry`, `job-cleanup`, `job-metrics`, `job-artifacts`, `job-artifact-get`, `job-reconcile`, `job-matrix`, the declared test plan.
  - `ci --remote`, `e2e --remote`.
  - Source upload: `deploy_exact_working_tree` (`sandbox/core/_remote.py:509`) uses git push over an SSH URL (`:2113`, `:2161`) and tar on SSH stdin for dirty files (`:2552`).
  - `workspace --remote` and `recovery --remote` use SSH-backed transports (status gates only partly traced).
  - `remote provision`, `remote ssh`, `remote service cleanup-broker`, `hermes repo auth github`.
  - `host apply`, `status`, `diagnose`, `logs`, `login-url`, `sync`, `recover`, and `host image` provision, settle and the default path.
- **HTTPS with control URL and bearer:** `wp --remote` and `resources --remote`.
- **Both:** the host disk-free read prefers HTTPS and falls back to SSH (`sandbox/commands/hosting.py:934-944`).
- **Local only:** `remote add`, `remove`, `set-origin`, `set-storage-floor`, `host plan`, `host stage`.
- **Not traced:** `exec --remote`, `test --remote` CLI mapping; `remote list`, `up`, `down`, `domains`, `docker-pool`, `plugins`, `pin`, `network-range`, `readiness`, `coverage`, `service status`, `service migrate`, `service stop`, `service capability`, nginx `edge`; the `sb deploy` CLI path (its upload primitive is SSH-only, above) and `sb preview`; `sync once`, `start`, `resolve`; `host destroy`, `heal`, `retire-delivery`.
- **Inferred, not run:** an HTTPS-only remote would pass the apply preflight (`hosting.py:973`) and then hit SSH-only `_remote_checked` (`hosting.py:867-913`).

## What Tailscale may change

Assumes "this server" is `xspeed-dev`. Confirm that, and what happens with `xcloud-london`. None of this has been investigated beyond reading code.

- The code already has a Tailscale mode: a non-loopback bind records `transport: tailscale` (`_remote.py:2703`), and `sb remote provision` has `--control https|tailscale`. Check how token work fits it.
- The PRD and requirement 7 assume `https://sandbox.xspeedcdn.online` and a public-internet service (PRD Problem, Scenario 3 and Constraints). Update them, and decide what connection details a token holder gets.
- **New question for the owner:** token holders would need tailnet access. Tailscale SSH or the tailnet's access rules could give them an SSH path to the node, which the "no SSH" decision forbids.
- If only tailnet members can reach the service, per-person tokens may still be wanted for attribution and revocation. The owner decides.
- Rank 1 above does not change with the transport.

## Test state

- **No tests exist for this feature and none were run.** Checks run so far: none, beyond `./sb guide --project-dir .` and read-only inspection.
- Baseline: the owner reports four tests in `tests/test_zone_coverage_targets.py` already fail on `origin/latest`. Not re-run here. This branch has no code change from that commit.
- CLI interpreter with pytest (per the owner's brief, not exercised here): `/Users/alim/Sites/git/.scratch/sandbox/medium-findings/venv/bin/python -m pytest tests/<file>.py -q`. It has no starlette, so it cannot run HTTP tests.
- MCP service tests need the server's own dependencies. The documented command from `tests/test_server_transport.py` is `mcp/wp-server/.venv/bin/python -m unittest tests.test_server_transport -v`. This worktree has no `.venv` there.
- `/Users/alim/Sites/git/sandbox/mcp/wp-server/.venv/bin/python` has mcp 1.28.1, starlette 1.3.1 and uvicorn 0.50.0, but `mcp/wp-server/requirements.txt` pins `mcp==1.25.0`. For real evidence, build a pinned venv under the scratch folder.
- New captured test subprocesses must use `tests.subprocess_support.synthetic_environment` (`AGENTS.md`).

## Design notes (proposals only, in no spec, none validated)

- Store under `$SANDBOX_HOME/runtime/remote-tokens/`, resolved through `sandbox.core._paths.BASE` in both CLI and service. `sandbox/core/_config.py:355` has an owner-only atomic replace to reuse, plus a flock. Refuse symlinks, loose modes and wrong owner.
- SHA-256 of a 256-bit random token. `hmac.compare_digest` on bytes, because non-ASCII strings raise `TypeError`, which would be a 500. Compare every record with no early exit. Re-read the file on each request.
- Token format `sbt-<hex>`: the redaction pattern `_BEARER` (`sandbox/services/redaction.py:39`) has no `_` in its character set, so an underscore prefix would escape it.
- CLI `sb remote token create|list|revoke`, using the positional idiom of `sb remote service`. Use `--person`, because `--label` is already a global instance option. Require either `--local` or a remote name. The controller path runs the installed `sb` through `ssh_process` and `remote_sb_path`; the token travels on SSH stdout and never on argv.
- Job attribution: `sandbox/jobs/models.py` is shape-fingerprinted and `registry.py` is off limits, so tie the label to `job_id` in a log line beside the ledger.
- Unverified, because the MCP source was not read: tool handlers probably cannot see a contextvar set in HTTP middleware (the session runs in its own task), and INFO logging may go nowhere under uvicorn without a handler.
- No new MCP tool may mint tokens.

## Security-review list so far

- The owner's chosen scope, and rank 1 above: holders can likely reach the host, and possibly root.
- Unsafe or unreadable token store: per-person tokens refused, shared token unaffected. This is an assumption the owner has not confirmed.
- Unsalted fast hash, justified by token entropy.
- No rate limiting on failed authentication is planned.
- The token store and service env file are readable by any host job.

## Files not needed to resume

The working evidence is in `/Users/alim/Sites/git/.scratch/sandbox/per-person-tokens/` (`trace-token-to-host.txt`, `ssh-flows-remote-cli.txt`, `ssh-flows-jobs.txt`, `prd-readiness-review.txt`, intermediate search output). Everything needed from them is above; they hold more line-level detail only.
