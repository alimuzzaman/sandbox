# Implementation Plan: Stack Lifetime and Per-Copy Stack Identity

**Branch**: `feat/stack-lifetime` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/077-stack-lifetime/spec.md`

## Summary

Every hosted target gets an authoritative record on the remote, one JSON file per
Compose project under `$SANDBOX_HOME/runtime/target-lifetimes/`, written under a host
flock by a stdlib-only program the controller ships over SSH (so it works before the
remote runtime is migrated). The record carries the lifetime (temporary, hosted,
permanent), its source, TTL and expiry, the state (active, reaping, reaped), merge
evidence and, for stacks, the stack identity (slug, copy id, owner, repo, ref,
commit, canonical flag, ports, hostnames). `sb host stack up` claims the slug and
project on the remote atomically before any worktree or submission; a holder that is
another owner or another copy is refused (`stack_slug_taken`). A host-side reaper,
run by the cleanup routine and by the controller-triggered safe-tier reap, tears down
temporary targets that are expired or carry merge evidence, re-checking the record
under its lock at execution and writing a deletion-manifest intent line for every
resource before it removes anything. Merge evidence is computed by a controller from
its own checkout (`git merge-base --is-ancestor`). No record means protected.

## Technical Context

**Language/Version**: Python 3.11+ on the controller; the shipped host program and
the reaper's host-side code use only syntax and stdlib available on Python 3.8+.

**Primary Dependencies**: existing `sandbox.commands.host_stack`,
`sandbox.hosting.stack_registry`, `sandbox.hosting.stack_template`,
`sandbox.hosting.destroy` (command builders), `sandbox.hosting.front_door.nginx`
(`transaction_command`, `CERT_ROOT`), `sandbox.core._remote` (`ssh_run`,
`remote_sb_path`), `sandbox.resources.cleanup_routine.run`,
`sandbox.resources.host_guard` (reclaim guard, apply-transaction probe),
`sandbox.secrets.ownership.journal.load_controller_id`, Git CLI.

**Storage**: remote `$SANDBOX_HOME/runtime/target-lifetimes/<compose_project>.json`
(0600), `.lock` (flock), `policy.json` (limits), `audit.jsonl` (append-only);
controller stack registry gains lifetime/copy/owner fields (version 1, additive).
Deletion intents go to the existing `runtime/resources/deletions/<run_id>.jsonl`.

**Testing**: `unittest` suites under `tests/`, run on xspeed-dev through
`sb job-start --remote xspeed-dev` by Haiku helpers; the host program is tested by
running it in-process against a temporary directory.

**Target Platform**: macOS/Linux controller; Linux remote with the nginx front door.

**Project Type**: CLI (`sb host stack up|list|down|ttl|sweep|reap|limits`,
`sb host classify`), plus the server-side cleanup routine.

**Performance Goals**: one extra SSH round trip per `stack up` (claim) and per
`stack list --remote`; the reaper adds one record-directory scan per routine run.

**Constraints**: a missing or invalid record never causes a deletion; every removal
has a manifest intent line first; guided xspeed-dev targets are never touched by this
work; no new control-plane payload (no `CONTROL_PROTOCOL_SPOKEN` bump); placeholders
only in code, tests and docs.

**Scale/Scope**: tens of stacks per remote (the stack port range is 100 ports).

## Constitution Check

- **I/II (per-project, registry)**: hosted targets are not WordPress instances; the
  instance registry is untouched. Target records are their own store, read only
  through the new module. PASS.
- **III (single entry, modular package)**: new package `sandbox/hosting/lifetime/`;
  dispatch through the existing `host stack` action and one new `host classify`
  action. No new top-level command. PASS.
- **IV (live proof)**: live `stack up --confirm` and a live reap on xspeed-dev need the
  orchestrator's migrate and authorization and are out of this branch. Evidence here:
  unit and CLI tests on xspeed-dev, plus the host program and reaper run against a
  temporary `SANDBOX_HOME`. Reported as a limit. PASS with stated limit.
- **V (idempotency, docs with code)**: claim is idempotent for the holding copy; the
  reaper resumes a `reaping` record; teardown tolerates absent resources. Docs
  (`docs/remote-hosting.md`, `skills/sandbox-cli/SKILL.md` and mirror, `CLAUDE.md`
  gotcha) land in the same change. PASS.
- **VI (parity before removal)**: nothing is removed. Hosted-site protection in the
  resources probe is unchanged; the reaper is an additional, opt-in-by-record pass
  that reuses `host destroy`'s command builders. PASS.

## Project Structure

### Documentation (this feature)

```text
specs/077-stack-lifetime/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/cli.md
├── contracts/host-store.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/lifetime/__init__.py
sandbox/hosting/lifetime/hoststore.py   # stdlib-only, self-contained: record types,
                                        # parse/validate, TTL, eligibility, limits,
                                        # flocked store, claim/update/release/list ops,
                                        # main(request) dispatcher (shipped over SSH)
sandbox/hosting/lifetime/naming.py      # owner label, repo identity, generated slugs
sandbox/hosting/lifetime/client.py      # controller side: ship hoststore over SSH,
                                        # typed results, refusals as HostingRefusal
sandbox/hosting/lifetime/merge.py       # controller-side merge check (git)
sandbox/hosting/lifetime/reaper.py      # host-side reaper: plan, re-check, manifest,
                                        # teardown via destroy/nginx command builders
sandbox/hosting/destroy.py              # extract edge-cleanup command builder (reuse)
sandbox/hosting/stack_registry.py       # StackRecord gains lifetime/copy/owner fields
sandbox/hosting/stack_template.py       # optional stack.dev_branch
sandbox/commands/host_stack.py          # up/list/down changes; ttl/sweep/reap/limits
sandbox/commands/host_classify.py       # sb host classify
sandbox/commands/hosting.py             # dispatch for classify (one branch)
sandbox/cli.py                          # new flags and actions
sandbox/resources/cleanup_routine/run.py  # hosted lifetime pass
sandbox/commands/resources.py           # safe-tier cleanup also runs the pass
tests/test_target_lifetime.py           # hoststore, naming, merge, reaper
tests/test_host_stack.py                # stack up/list/down/ttl CLI additions
docs/remote-hosting.md, skills/sandbox-cli/SKILL.md (+ .agents mirror), CLAUDE.md
```

**Structure Decision**: the record logic lives once, in a stdlib-only module that is
both imported (controller, reaper) and shipped verbatim over SSH (`python3 -c`), so
the controller's checks and the host's checks cannot drift. Side effects stay in
`client.py` (SSH), `reaper.py` (host shell) and the command modules.

## Phasing (one release phase)

1. Types and pure logic (hoststore records, TTL, eligibility, limits, naming).
2. Host store operations and the SSH client.
3. `stack up` claim, generated names, lifetime flags, ports/hostnames against remote
   records; `stack list --remote` from the remote; `stack down`/`ttl` ownership.
4. `host classify`, `stack limits`, `stack sweep` (merge evidence).
5. Reaper: routine pass, `stack reap`, safe-tier hook.
6. Docs, review, hand-back.

All phases ship together in one release cycle (phase manifest: this plan); the
orchestrator owns merge into `latest`, migrate and the xspeed-dev classification.

## Complexity Tracking

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| A module shipped verbatim over SSH | The remote must decide claims atomically across controllers, before the runtime is migrated | A control-plane action needs a protocol bump and a migrated runtime before any stack can be claimed |
| Reaper outside the resources probe | Teardown needs the nginx transaction and destroy's builders, which the self-contained probe cannot import | Injecting all of it into the probe would duplicate destroy and the nginx transaction |
