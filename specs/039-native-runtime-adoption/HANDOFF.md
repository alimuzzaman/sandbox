# Native Runtime Adoption Handoff

**Stopped:** 2026-08-02 at the user's request

**Branch:** `latest`

**Implementation checkpoint:** `611db13` (`Configure isolated native WordPress services`)

**Active Spec Kit feature:** `.specify/feature.json` points to this directory

## Objective and authoritative progress

Continue the coordinated host-ingress (037), TLD/DNS (038), and native-runtime
(039) adoption work through the full Spec Kit workflow, live proof, commit, and
push. Do not treat any of the three features as complete yet.

| Feature | Completed tasks | Open tasks | Status |
|---|---:|---:|---|
| 037 host ingress | 43 | 26 | Implementation and live proof incomplete |
| 038 TLD/DNS | 54 | 7 | Final integration/live proof incomplete |
| 039 native runtime | 38 | 40 | Active implementation feature |

The task files are authoritative; recount them before reporting status because
these numbers describe the stop point only.

## What is implemented in 039

- Compose remains the default. `managed_native` is an explicit machine-local
  selection and never silently falls back to Compose.
- A typed runtime registry, runtime service, ownership records, mode-switch
  refusal, truthful preflight, package planning, and TTY-only prerequisite
  confirmation are present.
- The privileged helper accepts fixed verbs and digest-bound plans only. It
  validates policy again under privilege, uses no-follow reads, a sanitized
  environment, and fixed root-owned paths.
- The managed guest design uses a bounded writable ext4 root image, a read-only
  project checkout at `/workspace`, private UID mapping, no-new-privileges,
  minimal durable capabilities, an AppArmor parent-to-guest transition, cgroup
  limits, seccomp, and a private veth.
- Networking is default-deny: there is no guest default route, nftables drops
  input/forward traffic for the interface (including IPv6), and active egress
  grants fail closed until an isolated broker exists.
- Exact Ubuntu Noble package planning, debootstrap into the image, masked service
  installation, nginx-or-Apache/PHP-FPM/MariaDB/cron configuration, and rollback
  plumbing exist. The WordPress document root is `/var/www/html`; project source
  is not used as the writable document root.
- Effective verification requires observed denial of host, sibling, metadata,
  and public reachability before services can activate.

No host packages were installed and no privileged image, machine, nftables, or
host-service mutation was performed at this checkpoint. Managed native remains
non-adoptable and has no live-proof evidence ID.

## Security-critical work still open

Resume from `tasks.md`, not from this summary. The immediate gaps are:

1. Reconcile the intentional native CLI registration with the exact architecture
   inventory test; audit the registry before changing its expected count.
2. Add integrity-bound WordPress core and WP-CLI artifacts without executing a
   remote installer on the host. Do not use Ubuntu's stale `wordpress` package.
3. Implement concrete out-of-band database credential staging/bootstrap without
   placing secrets in argv, environment variables, output, logs, or committed
   state.
4. Implement the privileged effective-state observer and prove every hostile
   boundary from the running guest.
5. Implement the isolated, scoped egress broker with revocation and counters;
   retain default-deny when no grant exists.
6. Finish managed adapter ensure/status/exec/test/destroy and route every
   WordPress/plugin/CLI/job execution path through the isolation gateway.
7. Add compare-before-remove recovery and cleanup, incumbent adapters/capability
   parity, docs, and the Ubuntu nginx/Apache hostile/coexistence/lifecycle proofs.
8. Return to the remaining 038 and 037 tasks and run their cross-spec live proofs.

Preserve the primary invariant: code running inside one instance, including a
plugin or CLI command, must not reach the host, sibling instances, metadata
services, or the public network unless an explicit scoped grant permits exactly
that destination.

## Validation at the stop point

The focused native-runtime suite passed:

```text
python3 -m unittest -q <33 native/runtime/isolation modules>
Ran 116 tests in 0.523s — OK
```

The full suite was also attempted:

```text
python3 -m unittest discover -s tests -p 'test_*.py'
Ran 1493 tests in 70.187s — FAILED (1 failure, 2 errors, 5 skipped)
```

Known failures to resolve or provision before claiming a green repository:

- `test_exact_owned_cli_and_mcp_inventories_are_enforced`: `COMMANDS` is 86 but
  the test still expects 85.
- Two `TestMcpHttpsTransportArguments` tests exit because the MCP venv has not
  been built in this checkout (`./sb mcp-install`).

The focused suite command and complete module list are recoverable from the git
session immediately preceding this handoff; all named 039 test modules under
`tests/test_isolation_*`, `tests/test_managed_*`, `tests/test_native_*`, and the
runtime contract/config/service modules passed.

## Resume procedure

1. Run `./sb guide --project-dir .`, inspect `git log -10`, and read
   `./sb skill show speckit-implement` plus the repository `AGENTS.md`.
2. Confirm branch `latest`, a clean tree, and `.specify/feature.json` still points
   to 039.
3. Read `prd.md`, `spec.md`, `plan.md`, `research.md`, `data-model.md`, contracts,
   `quickstart.md`, and the complete `tasks.md` before editing.
4. Work task-by-task, mark only proven tasks complete, and keep live-proof gates
   closed until hostile checks run on a normally booted Ubuntu 24.04 host.
5. Run proportionate checks, then commit and push each completed phase to
   `origin/latest`. Never install packages or make privileged host changes without
   the specified interactive confirmation path.
