# Quickstart: validating remote VPS hosting

Live-verification scenario (Constitution Principle IV — unit tests alone don't count as
done). This feature's core claim — that Model B's co-location makes the existing tool
surface work over a remote MCP connection with no code change — can ONLY be proven
against a real VPS. Unlike prior features this session (plugin-check, cross-platform
DNS), there is no way to fake this with a local Docker container standing in for "the
remote": the whole point under test is the network boundary itself.

## Prerequisites

- A real, reachable VPS the tester has SSH access to (a cheap short-lived cloud VPS is
  fine — this is a spike, not a permanent fixture). Ubuntu 24.04 or similar recommended
  to match `scripts/install-remote.sh`'s tested path.
- A Tailscale account (free tier is sufficient) for both the tester's machine and the
  VPS to join the same tailnet.
- Never point this at a real user-facing production server — treat the VPS as fully
  disposable test infrastructure for this verification pass.

## Phase 0 spike (do this BEFORE trusting the rest of the design)

This is the PRD's own Phase 0 recommendation (§8) — validate Model B's core claim before
writing more product code on top of it:

1. Manually install Tailscale + join the tailnet on both the tester's machine and the
   VPS (`tailscale up`).
2. On the VPS: install Docker, clone this repo, run `./sb mcp --transport=streamable-http
   --bind <vps-tailscale-ip> --port 9174 --token <a-test-token>` (or whatever the actual
   flag surface ends up being per implementation — the point of the spike is proving the
   MECHANISM, flag names can still be in flux).
3. On the tester's local machine, register a temporary MCP server pointed at
   `http://<vps-tailscale-ip>:9174` with the matching bearer token.
4. From that connection: call `ensure_instance` for a small test project already present
   on the VPS (a `git clone` of any small WP plugin is fine for this spike), then
   `fs_read` a file inside it, `visit` its URL and confirm a real screenshot comes back
   (not blank/error), and run a `wp_cli` command.
5. **Goal**: confirm all four calls return REAL, correct, VPS-side data — not empty,
   not stale, not a connection error. This is the single most important thing to prove;
   if it doesn't work, the whole feature's premise needs re-examining before continuing.

## Scenario 1: register + provision a fresh remote

```bash
./sb remote add spike-vps ssh://ubuntu@<vps-ip>
./sb remote list          # shows spike-vps, reachable, not yet provisioned
./sb remote provision spike-vps
./sb remote list          # shows spike-vps, reachable, provisioned: true
```

Expected: provisioning completes without manual SSH steps; a second `provision` run
(idempotency check, spec FR-005) succeeds cleanly rather than erroring or duplicating
state.

## Scenario 2: deploy a real project, including uncommitted changes

Using a real (or scratch) plugin project directory:

```bash
cd ~/some/plugin/project
echo "// spike marker" >> some-existing-file.php   # an uncommitted tracked-file edit
echo "spike" > a-brand-new-untracked-file.txt        # an uncommitted untracked file
./sb deploy --project-dir . --remote spike-vps
```

Expected: the output names the pushed commit and reports "2 uncommitted files applied."
SSH in and directly diff the VPS's checkout against the local working tree — they MUST
be byte-identical (spec SC-002).

## Scenario 3: re-deploy replaces, doesn't stack

```bash
git checkout -- some-existing-file.php   # revert the local uncommitted edit
./sb deploy --project-dir . --remote spike-vps
```

Expected: the VPS's copy of `some-existing-file.php` no longer has the "spike marker"
line — confirming the reset-then-apply-fresh sequence actually replaces rather than
leaving the first deploy's diff still partially applied underneath.

## Scenario 4: run a full instance on the remote and use it like a local one

There is no `--remote` flag on `ensure`/`wp_cli`/etc. — per this feature's design (see
`contracts/cli-and-mcp.md`), you reach a remote instance by SSHing to the VPS and running
`sb` there directly, exactly as if you were on the VPS itself, since it's a full
co-located `sb` install:

```bash
ssh ubuntu@<vps-ip> "cd \$SANDBOX_HOME/deploy-src/<project-slug> && ./sb ensure --project-dir ."
```

Then, via the SECOND registered MCP server (`sandbox-spike-vps`), exercise: a `wp_cli`
command, a `fs_read`, a `visit` with screenshot, and `run_tests`. Compare against running
the identical operations locally for the same project — results should differ only in
WHERE they ran, never in correctness.

## Scenario 4b: a local AND a remote instance for the same project never collide (FR-012)

Flagged by `/speckit-analyze`: FR-012's "never silently conflated" guarantee currently
rests entirely on the architectural argument in `plan.md` (separate registries, separate
MCP servers) — nothing exercises it directly. Prove it here:

1. With the SAME project directory used in Scenario 4, also boot a LOCAL instance:
   `./sb ensure --project-dir .` (from your local machine, no SSH).
2. Confirm both instances are simultaneously usable: a `wp_cli` call through the local
   `sandbox` MCP server, and the SAME kind of call through `sandbox-spike-vps`, run
   back-to-back.
3. Confirm they're genuinely independent — e.g. write a distinct marker value via
   `wp option update` on each, then read it back through the OTHER connection and
   confirm it does NOT see the other instance's value (no shared state, no
   registry/database bleed between local and remote).
4. Confirm `./sb instances` (local) lists only the local instance, and never shows the
   remote one — the two are only ever visible through their own machine's registry.

## Scenario 5: local behavior is provably unchanged

With no remote targeted at all, run the existing local test suite and a normal local
`ensure`/`wp_cli` session. Confirm zero behavior difference from before this feature
existed (spec FR-015/SC-004) — this is the release gate.

## Cleanup

- `./sb remote remove spike-vps` locally.
- Tear down the spike VPS entirely (it was disposable test infrastructure, per
  Prerequisites) — do not leave it running or billing after this pass.
