# Quickstart: Remote Hermes Agent Integration

This is the intended operator journey for implementation and acceptance. Commands shown for V2 and V3 are milestone contracts, not authorization to update, expose, deploy, change DNS, or provision secrets without current approval.

## 1. Verify the existing remote

```bash
./sb remote list --json
./sb hermes doctor --remote scaleway-sandbox --json
```

Expected before installation: the remote is reachable/provisioned, platform and Docker checks pass, and Hermes reports `absent` rather than an SSH/path error.

## 2. Install the supported pinned release (V1)

```bash
./sb hermes install \
  --remote scaleway-sandbox \
  --version v2026.7.7.2 \
  --json
```

The result must include a full verified commit. Re-run the same command and confirm it reports an idempotent healthy/reconciled result.

## 3. Configure provider access and Sandbox MCP

Interactive provider setup is operator-owned:

```bash
./sb hermes setup --remote scaleway-sandbox
./sb hermes doctor --remote scaleway-sandbox --json
```

Doctor must confirm:

- the Hermes launcher and pinned commit;
- direct remote `sb` execution;
- the remote `$SANDBOX_HOME` path;
- the effective registered `sandbox` stdio MCP server and a successful
  initialization of that server;
- complete Sandbox tool/resource/prompt discovery;
- sequential MCP calls;
- manual terminal approvals and denied dangerous cron commands;
- no secret values in output.

## 4. Authenticate and clone a repository

```bash
# Create a fine-grained GitHub token scoped to only the selected repository.
# Do not grant organization permissions or use the browser OAuth flow.
read -r -s GH_FINE_GRAINED_TOKEN
printf '\n'
printf '%s' "$GH_FINE_GRAINED_TOKEN" | ./sb hermes repo auth github \
  --remote scaleway-sandbox --token-stdin
unset GH_FINE_GRAINED_TOKEN
./sb hermes repo clone --remote scaleway-sandbox \
  --url https://github.com/OWNER/REPO.git \
  --name repo
./sb hermes repo list --remote scaleway-sandbox --json
```

For the token, choose the repository owner, **Only select repositories**, the
one intended repository, no organization permissions, and only the required
`Contents` access (`Read and write` for code changes; `Read-only` for
inspection). Also verify that a URL containing `user:token@host`, a traversal
name, and a duplicate conflicting name are rejected before clone.

## 5. Start worktree-isolated sessions

Interactive:

```bash
./sb hermes chat --remote scaleway-sandbox --repo repo
```

One-shot asynchronous:

```bash
./sb hermes run \
  --remote scaleway-sandbox \
  --repo repo \
  --prompt "Inspect the repository and report its test command; do not modify files." \
  --async \
  --json

./sb hermes job status \
  --remote scaleway-sandbox \
  --job-id JOB_ID \
  --json
```

Start two sessions and confirm their worktree paths/branches differ and the primary checkout's status/diff remain unchanged. Use `--no-worktree` only for an intentionally non-isolated session.

## 6. Create a Sandbox instance on demand

From a Hermes session in a WordPress-capable repository, instruct Hermes to:

1. Call Sandbox `ensure_instance` with the current worktree as `project_dir`.
2. Use the returned instance URL.
3. Call other Sandbox tools only after the instance exists.

Verify the instance appears in the remote Sandbox registry exactly once for that worktree. A plain non-WordPress Git repository must not create an instance during session startup.

## 7. Configure the gateway (V1)

```bash
./sb hermes gateway setup --remote scaleway-sandbox --allow operator-id-or-channel
./sb hermes gateway install --remote scaleway-sandbox
./sb hermes gateway start --remote scaleway-sandbox
./sb hermes gateway status --remote scaleway-sandbox --json
./sb hermes gateway logs --remote scaleway-sandbox --lines 100 --json
```

Before a successful start, configure at least one explicit allowed identity/channel. Verify empty and wildcard policies are refused.

## 8. Complete the V2 operational gate

```bash
./sb hermes update plan \
  --remote scaleway-sandbox \
  --version NEXT_SIGNED_TAG \
  --json

# Requires separate current approval after reviewing the plan:
./sb hermes update apply \
  --remote scaleway-sandbox \
  --version NEXT_SIGNED_TAG \
  --confirm \
  --json

./sb hermes backup list --remote scaleway-sandbox --json
./sb hermes health --remote scaleway-sandbox --json
./sb hermes acceptance v2 --remote scaleway-sandbox --json
```

The gate passes only after injected update failure/rollback, restore, configured resource-limit rejection, stale-state reconciliation, log rotation, and reboot recovery have all produced passing evidence.

### V2 fault-injection and reboot procedure (separately approved)

Run the following only in an approved disposable or recovery-tested remote
window. Do not create a passing acceptance record by hand. Record the current
Sandbox commit, full Hermes commit, UTC timestamps, command exit status, and
sanitized observations in `$SANDBOX_HOME/runtime/hermes.json`.

1. Capture `doctor`, `health`, `policy show`, `backup list`, and `update plan`
   output. Confirm the target is an immutable signed tag and full commit.
2. Create a backup and verify its SHA-256 sidecar. In a disposable fixture,
   corrupt a copy of the archive and confirm restore refuses it; restore only
   the intact archive with separate `--confirm` approval and verify `health`.
3. Inject a post-update health failure against an approved test target. Verify
   `update apply --confirm` reports rollback, then verify the original launcher
   revision, profile, and gateway service are healthy.
4. Temporarily lower one policy limit at a time (job count, worktree count,
   free disk, and free memory) and submit a harmless one-shot job. Verify the
   request is rejected before launch, then restore the original policy.
5. End a disposable detached process without writing its completion marker and
   leave both a clean and a dirty worktree. Verify status identifies stale
   state; cleanup may remove only the clean inactive worktree after `--confirm`.
6. Verify bounded job/gateway logs and configured retention. Reboot only after
   explicit approval, then verify Sandbox registry entries are intact, enabled
   services recover, and `doctor` reports stale sessions requiring manual work.
7. Run `./sb hermes acceptance v2 --remote scaleway-sandbox --json`. It must
   remain pending unless all five pieces of evidence are current and recorded
   against the installed Hermes commit. A stale revision invalidates the gate.

## 9. Install the dashboard only after V2 (V3)

Before the V2 gate, this must fail with `v2_gate_required` and make no changes:

```bash
./sb hermes dashboard install --remote scaleway-sandbox --json
```

After V2 passes:

```bash
./sb hermes dashboard install --remote scaleway-sandbox
./sb hermes dashboard setup --remote scaleway-sandbox --port 9119
./sb hermes dashboard start --remote scaleway-sandbox
./sb hermes dashboard doctor --remote scaleway-sandbox --json
```

Open an authenticated SSH tunnel from the operator machine:

```bash
ssh -N -L 9119:127.0.0.1:9119 <configured-scaleway-sandbox-ssh-target>
```

Then visit `http://127.0.0.1:9119`. Confirm the remote service listens only on loopback and uses the same profile, skills, MCP catalog, sessions, gateway, and cron state as CLI Hermes.

## 10. Optional public exposure (V3, separately approved)

```bash
./sb hermes dashboard expose \
  --remote scaleway-sandbox \
  --fqdn hermes.example.com \
  --plan \
  --json
```

Only after reviewing the plan, configuring supported OAuth outside version control, confirming feature 015 is available, and receiving current approval:

```bash
./sb hermes dashboard expose \
  --remote scaleway-sandbox \
  --fqdn hermes.example.com \
  --confirm \
  --json
```

Acceptance requires TLS, rejected unauthenticated access, successful authenticated health, and rollback proof. `--insecure` is never supported.

## 11. Focused verification commands

```bash
python -m unittest tests.test_hermes tests.test_cli tests.test_mcp
python -m unittest discover -s tests
git diff --check
```

Live acceptance artifacts must state the remote, Sandbox commit, full Hermes commit, checks run, sanitized result, and residual risks without recording credentials or prompt content.

## Implementation verification (2026-07-10)

The local V1 control-plane implementation was verified without changing a
remote host:

```bash
python3 -m unittest tests.test_hermes tests.test_cli -v
/Users/alim/Sites/git/sandbox/.cli-venv/bin/python -m unittest discover -s tests -q
```

Results: 19 focused tests passed; 315 full-suite tests passed with two existing
environment-dependent skips. The shared MCP virtual environment also confirmed
that `hermes_status` and `hermes_run` are registered. The remote install,
provider authentication, repository clone, instance creation, gateway service,
and V2/V3 acceptance steps were not run.

## Implementation verification (2026-07-11)

```bash
/Users/alim/Sites/git/sandbox/.cli-venv/bin/python -m unittest tests.test_cli tests.test_hermes -q
/Users/alim/Sites/git/sandbox/.cli-venv/bin/python -m unittest discover -s tests -q
git diff --check
```

Results: 78 focused tests and 389 full-suite tests passed; one existing test
was skipped. The suite emits pre-existing subprocess `ResourceWarning`s and
intentional negative-path diagnostic messages, but exited successfully. No V2
destructive recovery action, reboot, or dashboard operation was run in this
implementation pass.

Remote V1 configuration verification on `scaleway-sandbox` also passed. An
idempotent install/setup invocation was followed by an immutable update plan:
the installed and target Hermes commits both resolved to
`9de9c25f620ff7f1ce0fd5457d596052d5159596`, with no update required.
`doctor` confirmed direct Sandbox CLI access, a registered effective Sandbox
MCP entry, a complete Sandbox MCP catalog, the owner-only integration profile,
locking/session utilities, disk, and memory. An operator-owned model login then
completed a read-only Hermes smoke against a managed repository. A public
WordPress plugin clone was isolated in
`$SANDBOX_HOME/runtime/hermes-worktrees/<repository>/`; its primary checkout
remained clean, and `sb ensure` plus `sb doctor` confirmed a reachable,
REST-authenticated WordPress instance for that worktree. GitHub CLI is now a
documented prerequisite for the separate interactive GitHub device flow.
At the time of that earlier implementation pass, the clean-install gate,
gateway start, and V2 destructive recovery/reboot checks remained unrun; the
current live V2/V3 evidence is recorded below.

## Live V2/V3 acceptance (2026-07-11)

The separately approved recovery window on `scaleway-sandbox` completed
against Hermes commit `9de9c25f620ff7f1ce0fd5457d596052d5159596`. The remote
state records successful revision-bound evidence for update rollback, verified
backup restore, resource preflight rejection, stale-session reconciliation,
and reboot recovery. The authoritative command below returned `ok: true` with
no missing checks:

```bash
./sb hermes acceptance v2 --remote scaleway-sandbox --json
```

V3 was then exercised with the V2 gate in force:

```bash
./sb hermes dashboard install --remote scaleway-sandbox --port 9119 --json
./sb hermes dashboard setup --remote scaleway-sandbox --port 9119 --json
./sb hermes dashboard restart --remote scaleway-sandbox --port 9119 --json
./sb hermes dashboard doctor --remote scaleway-sandbox --port 9119 --json
```

The installer created the isolated Hermes virtualenv with the upstream
`web`/`pty` extras. The service is enabled with user lingering, listens only on
`127.0.0.1:9119` after the controlling SSH session closes, and the remote
loopback HTTP probe returned `200`; `dashboard doctor` returned healthy.
The optional public path was exercised only through its fail-closed preflight:
`dashboard expose --fqdn hermes.sandbox.example.com --json` returned
`feature_015_required` and did not create a route, DNS record, or public
endpoint. Public OAuth/TLS deployment and rollback remain conditional on the
separate feature-015 managed-hosting capability and a future explicit approval.

Local regression for this acceptance/fix phase passed:
`python -m unittest tests.test_hermes tests.test_cli -q` (87 tests) and
`python -m unittest discover -s tests -q` (399 tests, one existing skip).
The full suite emitted its existing subprocess warnings and intentional
negative-path fixture diagnostics but exited successfully.
