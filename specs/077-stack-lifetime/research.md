# Research: Stack Lifetime and Per-Copy Stack Identity

## R1. Where the authoritative record lives

- **Decision**: one JSON file per Compose project at
  `$SANDBOX_HOME/runtime/target-lifetimes/<compose_project>.json` on the remote, all
  mutations under `runtime/target-lifetimes/.lock` (flock), atomic temp-and-rename,
  plus an append-only `audit.jsonl`.
- **Rationale**: the server-side reaper reads it without the controller; a flock on
  the host makes claims atomic across controllers; one file per target keeps a
  corrupt file from hiding the others.
- **Alternatives**: controller registry only (cannot see other controllers, the
  takeover bug); a control-plane `/resources` action (needs a protocol bump and a
  migrated runtime before any claim works); one big JSON (one bad write hides all).

## R2. How the controller reaches the store

- **Decision**: ship `sandbox/hosting/lifetime/hoststore.py` source verbatim with
  `ssh_run(entry, shlex.join(("python3", "-c", program)), input_data=request_json)`,
  the pattern `hosting.py` already uses for its candidate probe. The program resolves
  `${SANDBOX_HOME:-$HOME/sandbox}` itself.
- **Rationale**: works on any remote with system `python3`, before migrate; the same
  module is imported by the controller and by the host reaper, so checks cannot
  drift. The module is stdlib-only with no package imports.
- **Alternatives**: running the remote's `sb-src/sb` (requires the migrated runtime
  for every claim); shell snippets (no atomic read-check-write).

## R3. How server-side cleanup tears a target down

- **Decision**: a host-side reaper (`sandbox/hosting/lifetime/reaper.py`) runs in the
  cleanup routine process on the host, after its busy checks and while holding the
  reclaim guard, before the workspace plan. It re-reads each record under the store
  lock, marks it `reaping`, lists the target's containers, volumes, networks and own
  image tags, writes one manifest intent line per resource and per path, then runs
  `destroy._remote_teardown_command`, the nginx route removal
  (`front_door.nginx.transaction_command(..., remove=True)`) and the certificate
  removal locally through `sh -c`. On success the record becomes `reaped`.
- **Rationale**: reuses `host destroy`'s exact removal commands (parity, principle
  VI); the routine already runs the host's own `sb`, so the package is importable.
- **Front door**: like `host destroy`, only nginx is supported; a Caddy target is
  skipped with `front_door_capability_unavailable` and stays.
- **Controller-triggered reap**: `sb host stack reap --remote R --confirm` and
  `sb resources cleanup --tier safe --remote R --confirm` run
  `<remote_sb_path> host stack reap --here --confirm --json` over SSH; an
  unmigrated runtime is reported (`stack_reap_unavailable`), not worked around.
- **Alternatives**: inject into the self-contained resources probe (would duplicate
  destroy and the nginx transaction); controller-only `destroy.execute` (needs the
  controller online and its credentials; the routine must work alone).

## R4. What the server cannot remove

- **Decision**: per-hostname Cloudflare records (exact DNS mode) and the controller's
  hosting state stay after a server-side reap. The reaped record lists the hostnames
  with `dns_left: true`; `sb host stack down` (or `host destroy`) from a controller
  finishes, through the unchanged `destroy.execute`, which tolerates absent remote
  resources. Zone-coverage dependents are released by that same controller step.
  Wildcard coverage leaves no per-target record, so a wildcard stack is fully gone
  on the server after the reap.

## R5. Merge detection

- **Decision**: `git fetch --quiet origin <dev_branch>` then
  `git merge-base --is-ancestor <commit> origin/<dev_branch>` in a controller
  checkout whose normalized origin matches the record's `repo`. Exit 0 = merged,
  1 = not merged, anything else = unknown (no evidence). Runs after a confirmed
  canonical dev `stack up` and on `sb host stack sweep --remote R [--confirm]`.
  Evidence is written only if the record's commit still equals the checked commit.
- **Rationale**: uses only existing Git access; the server has no Git history.
- **Limit**: squash/rebase merges are not reachable and fall back to the TTL.

## R6. Owner and copy

- **Decision**: owner = `{label, controller}`; controller = the existing controller id
  (`sandbox.secrets.ownership.journal.load_controller_id`, file
  `$BASE/runtime/secret-ownership/controller-id`); label = `--owner` or the template
  checkout's `git config user.email`, required. Copy id = 8 hex chars generated at
  the first `up`, stored in the controller stack registry.
- **Trust**: accident prevention only; everyone with SSH access can edit the files.

## R7. Generated names

- **Decision**: `<branch-short>-<owner-short>-<n>`: branch = last path segment of
  `ref_branch` (or `c<sha7>` when detached), lowercased to `[a-z0-9-]`, max 16;
  owner = local part of the label, same rule, max 8; n = lowest integer >= 1 not used
  by a record on the remote nor in the controller registry. Shortened until the
  template's slug pattern and the safe slug rule match; if no counter form matches,
  `<branch-short>-<4 hex>`. A claim that loses a race retries the next n (3 tries).

## R8. Limits

- **Decision**: `policy.json` on the remote:
  `{"per_owner": 5, "total": 40, "min_free_bytes": 21474836480}` when absent.
  Per-owner counts active temporary stack records with the same owner label; total
  counts every non-reaped record; free disk = min free bytes of `$SANDBOX_HOME` and
  `/var/lib/docker` (missing path ignored). Only a new copy is checked. Set with
  `sb host stack limits --remote R ... --confirm`.

## R9. Pre-feature stacks

- **Decision**: a stack recorded in this controller's registry with no remote record
  is claimed on its next `up` by that controller (the claim passes `legacy: true`
  and is accepted only when no record holds the slug or project). Until then it is
  unclassified and protected; `stack list` shows it as `remote_record: missing`.

## R10. Stray non-compose container (orchestrator side note)

- **Finding**: `workspace_teardown.teardown_workspace_stack` refuses the whole
  workspace when a container bound to the checkout has no Compose label
  (`bound_container_not_exclusive`), so the checkout is kept and the reap reports
  `stack_teardown_incomplete`. This is a deliberate exclusivity guard in the
  injected probe; changing it is a deletion-safety change outside this feature.
  Recommended follow-up (not built here): treat an unlabeled container whose every
  bind source lies inside that workspace checkout as owned, and record it in the
  manifest before removal. Operators can remove the stray container by hand today.
