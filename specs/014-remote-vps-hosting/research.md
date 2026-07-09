# Phase 0 Research: Remote VPS hosting for sandbox instances

No `NEEDS CLARIFICATION` markers remained in the plan's Technical Context — the source
design was already fully resolved in `docs/remote-hosting-prd.md` (a dedicated
feasibility study) and a follow-up planning conversation recorded in that doc's §0, before
this spec/plan were written. This document records the concrete implementation decisions
made while translating that resolved design into sandbox's own conventions.

## Decision: registries stay separate; no shared `runtime` field

**Decision**: each machine (local, and each remote VPS) keeps its own fully independent
`$SANDBOX_HOME/runtime/registry.json`. No new field is added to the registry schema, and
no v2→v3 migration is needed.

**Rationale**: the PRD's original sketch (§4.2) proposed a `runtime` field so one shared
registry could describe both local and remote instances. But Model B's whole premise is
co-location: the remote MCP server is the SAME `mcp/wp-server` codebase running on the
VPS with its own local `$SANDBOX_HOME`. A remote instance is therefore already fully
described by the VPS's own registry — there's nothing to reconcile into a shared one.
Users disambiguate local vs. remote by which of the TWO registered MCP servers they call
(`sandbox` vs `sandbox-<remote-name>`), not by a field on a shared record. This also
satisfies spec FR-012 (local and remote instances for the same project can never be
silently conflated) by construction, not by additional enforcement code.

**Alternative considered**: the PRD's original shared-registry-with-`runtime`-field
design (Model C's direction). Rejected for Phase 1 — it's real, useful complexity for a
FUTURE shared/multi-tenant registry view, but nothing in this feature's actual user
stories needs a single registry spanning two machines. Revisit only if a later feature
genuinely needs "list every instance I have, local and remote, in one place."

## Decision: `remotes:` config block mirrors `_licensing.py` exactly

**Decision**: add a `remotes:` top-level block to `sandbox.local.yml`, read/written via
new `_remote_block()` / `_write_remote_block()` functions in `sandbox/core/_remote.py`,
copying `sandbox/core/_licensing.py`'s `_licensing_block()`/`_write_licensing_block()`
shape verbatim: read-modify-write preserving the rest of the file, `chmod 0o600` after
write, never echo the bearer token back to the user.

```yaml
remotes:
  myvps:
    ssh: "ubuntu@203.0.113.10"
    tailscale_host: "myvps.tailnet-name.ts.net"   # recorded after provision
    mcp_port: 9174
    bearer_token: "<secret, shown once at mint time, never echoed again after>"
    provisioned: true
```

**Rationale**: this is the exact existing precedent for "per-machine secret config that
isn't project state" in this codebase (also used by `_bridge.py`'s snapshot-bridge
token). No new config-storage mechanism needed.

**Alternative considered**: a separate `remotes.json` file. Rejected — `sandbox.local.yml`
is already the established, gitignored, `chmod 0600`-protected secret store; a second
file would just fragment where secrets live for no benefit.

## Decision: deploy mechanism — git push (denyCurrentBranch=updateInstead) + diff-apply

**Decision**: `sb remote provision` ensures each project has a plain (non-bare) git repo
on the VPS at a deterministic path (derived from the project's canonical slug/name — the
SAME canonicalization `sandbox_core.find_project_root`/`_canonical` already use, so both
sides agree on the path without extra bookkeeping) with
`git config receive.denyCurrentBranch updateInstead` set, lazily created on that
project's FIRST `sb deploy` to that remote (not during `provision`, which is
machine-level, not project-level). `sb deploy` then:

1. `git push <vps-remote-url> HEAD:refs/heads/<current-branch>` — pushes only new objects.
2. Over the same SSH connection, resets the VPS working tree to that just-pushed HEAD
   (`git reset --hard <sha>`) — this is what makes step 3 idempotent/replacing rather
   than stacking.
3. Captures the local uncommitted state — `git diff` for tracked-file changes, PLUS a
   separate pass for untracked files (`git status --porcelain` filtered to `??` entries,
   respecting `.gitignore` since that's what `git status` already does) — and applies it
   on the VPS (tracked diff via `git apply`; untracked files via a direct file copy over
   the same SSH connection, e.g. `tar`+SSH or `scp`, since there's no git object for them
   to apply from).

**Rationale**: matches spec FR-006/FR-007/FR-008 exactly (works for unpushed branches,
one-way, replace-not-stack). `receive.denyCurrentBranch=updateInstead` is a real,
long-standing git feature (documented in `git help config`) purpose-built for "push
directly into a checked-out working repo," avoiding a bare-repo + separate-checkout
dance.

**Alternative considered**: `rsync` of the whole working tree, `.gitignore`-aware (this
session's plugin-check feature's `.distignore` auto-detection was raised as a precedent).
Rejected for the COMMITTED layer — git push is more efficient (delta compression, only
new objects) and self-documenting (the VPS's checkout IS at a specific, known commit).
Rsync remains the right tool for the UNCOMMITTED-untracked-file sub-case specifically,
since there's no git object to diff.

## Decision: MCP transport — `server.py` gains a mode flag, tool files stay untouched

**Decision**: `mcp/wp-server/server.py` gains a transport-selection branch: default
(unchanged) is `mcp.run()` over stdio for local use; a new "remote server mode" (invoked
by `sb remote provision`'s setup, running ON the VPS) calls
`mcp.run(transport="streamable-http", host=<tailscale-bound-address>, port=<port>)` with
bearer-token auth. Every file under `mcp/wp-server/tools/*.py` is completely unchanged —
they already only touch local paths/the local Docker daemon, which is exactly what's
true on the VPS too.

**Rationale**: confirmed via the `mcp` package's own support for `streamable-http`
transport (already researched in the PRD). This is the minimal change that makes Model B
real: co-location means the existing tool code doesn't need to become "remote-aware" at
all.

**Alternative considered**: a `remote: str | None` parameter threaded through every
existing tool function (Model C's shape from the PRD). Rejected for Phase 1 — that's the
right shape IF a single MCP server needs to reach multiple backends from one process,
which isn't this feature's model (two separate MCP server processes, one per machine).

## Decision: provisioning script — new `scripts/install-remote.sh`, run over SSH

**Decision**: `sb remote provision <name>` SSHes to the registered host and runs a new
`scripts/install-remote.sh` (copied over first, or piped via `ssh host bash -s <
install-remote.sh`), which installs/joins Tailscale, installs Docker CE + compose plugin
(reusing the exact package-manager-detection logic already added this session in
`sandbox/core/_docker.py` for apt/dnf/pacman/zypper), clones/sets up the `sb` runtime
itself, and provisions the `visit` tools venv (Playwright + headless Chromium — required
server-side per the PRD, since `visit` must reach `localhost:<port>` and the VPS's own
`.tst` proxy).

**Rationale**: mirrors `scripts/install-macos.sh`/`scripts/install-ubuntu.sh`'s existing
one-shot bootstrap-script shape, just executed remotely via SSH instead of locally via a
direct shell invocation — same idempotency expectations (spec FR-005), same "one command,
no manual copy-paste" goal (spec SC-001).

**Alternative considered**: an Ansible/cloud-init-based provisioning tool. Rejected as
unnecessary weight for Phase 1's single-VPS, single-developer scope — a shell script
matches the project's existing bootstrap-script convention and needs no new dependency.

## Decision: `sb deploy`'s target project path resolution

**Decision**: the VPS-side path for a given project's deploy-target git repo is derived
deterministically as `$SANDBOX_HOME/deploy-src/<canonical-project-slug>` on the VPS,
where `<canonical-project-slug>` uses the exact same resolution
(`sandbox_core._project_slug`/`_canonical`) already used locally — so `sb deploy` never
needs to ask the VPS "where do you keep this project," it computes the same answer the
VPS itself would.

**Rationale**: avoids inventing a NEW path-mapping/registration step (the PRD's §7 Q-open
item about "client↔server path translation" reduces to "run the same deterministic
function on both sides," which is simpler than the PRD anticipated when it assumed a
continuously-synced arbitrary path).

**Alternative considered**: an explicit per-project `remotePath` config field the user
sets manually. Rejected — adds a redundant manual step for something already
deterministically derivable, the same simplification lesson from this session's
plugin-check `pluginCheck.slug` removal.

## Still open (deferred to `/speckit-tasks`/implementation, not blocking this plan)

- Screenshot/artifact return format (spec's source description flagged this as open):
  since `visit`/`pixelmatch_diff` already return results over the MCP transport
  (streamable-http supports binary/base64 payloads same as stdio does today), the
  simplest Phase 1 answer is inline base64, identical to how these tools already behave
  locally — no new fetch-by-id endpoint needed unless a real size problem is observed
  during live verification. Not a design gap serious enough to block planning.
