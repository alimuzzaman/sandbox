# scaleway-sandbox: where 185 GiB went, and the two structural causes

Date: 2026-08-16. Host `scaleway-sandbox` (`alim@212.47.72.49`), single ext4 `/dev/sda1`,
193 GB total. Read-only diagnostic. No deletion, prune, or config change was performed.

Companion note: `scaleway-sandbox-cache-unknown-space-2026-08-16.md` (why `sb resources`
could not attribute the space).

---

## 1. Measured layout

`df -h /` at 11:30 UTC: `193G size / 185G used / 8.4G avail / 96%`. (It was 97% and
768 MB free at 09:00; BuildKit's own GC freed ~9 GB during the audit.)

| Path | Size | Note |
|---|---|---|
| `/home/alim/sandbox/deploy-src` | **89.6 GiB** (93,921,900 KB) | 178 dirs |
| `/var/lib/docker` | 35 GiB | **volumes 35 GiB**; containers 67 MB; buildkit 39 MB |
| `/var/lib/containerd` | 30 GiB | snapshots 26 GiB + content blobs 4.2 GiB |
| `/home/alim/restore` | 8.63 GiB | `staging/home` 4.33 GiB, `staging/var` 2.55 GiB, dated 08-05 |
| `/home/alim/.t3` | 5.98 GiB | `.t3/runtime` 5.17 GiB |
| `/home/alim/sandbox/runtime` | ~5.9 GiB | ~70 `wp-*` instances; `.drive-volume-fallbacks-*` 2 x 492 MiB |
| `/home/alim/git` | 4.55 GiB | `git/lenzora` 3.53 GiB (a 2nd full clone outside deploy-src) |
| `/home/alim/.cache` | 3.47 GiB | `ms-playwright` ~2.43 GiB across 9 browser builds |
| `/home/alim/.codex` | 2.23 GiB | `sessions` 1.32 GiB, `logs_2.sqlite` 476 MiB |
| `/tmp` | ~1.2 GiB | `jest_rt` 452 MiB, `playwright-download-*` 177 MiB, v8/node compile caches 210 MiB |
| `/home/alim/.npm` / `.nvm` | 887 / 471 MiB | |
| `/home/alim/quarantine` / `sandbox-matrix` | 171 / 32 MiB | |
| `/var/log` | 846 MiB | `auth.log` 41 MiB |

Inside `deploy-src`:

- **`.pnpm-store`: 19 dirs = 44.5 GiB** (8 x ~3.5 GiB, 5 x ~2.0 GiB, 4 x ~1.5 GiB, 1 x 1.7, 1 x 0.65)
- **`.git`: 177 dirs = 19.2 GiB** (avg 114 MiB; evaluation family 353 MiB, lenzora 190 MiB, t3code 410 MiB)
- **host `node_modules` (top level): 87 dirs = 9.83 GiB**

Docker accounting (`docker system df`, 11:30):

```
Images         37 total / 20 active   14.02GB   reclaimable -1.34e+09B (accounting artifact)
Containers     86 total / 64 active    1.634GB  reclaimable 347.2MB
Local Volumes  71 total / 39 active   30.56GB   reclaimable 872.3MB
Build Cache   106 total / 13 active   10.76GB   reclaimable 0B
```

## 2. `/var/lib/containerd` (30 GiB) is Docker's own store, not a second runtime

```
docker info -> Storage Driver: overlayfs / driver-type: io.containerd.snapshotter.v1
ctr namespaces list -> moby, moby_history   (nothing else)
which ctr nerdctl crictl k3s kubelet -> only /usr/bin/ctr
```

Docker 29.7.2 here uses the **containerd image store**, so image layers, container
writable layers, and the BuildKit cache all live in
`/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots`.
`/var/lib/docker` retains only volumes/containers metadata — hence `/var/lib/docker` 35 GiB
being 35 GiB of *volumes*. **Never delete inside `/var/lib/containerd` directly**; reclaim
it only via `docker image prune` / `docker builder prune`.

## 3. Root cause A — pnpm store is duplicated per workspace (44.5 GiB) AND copied into
per-workspace volumes (25.4 GiB)

Measured container config (`docker inspect sandbox-lenzora-workspace-a655c1f7126dc1-test-1`):

```
IMG   node:24.18.0-bookworm-slim      WD /workspace      USER (empty -> uid 0)
CMD   sh -lc corepack enable && pnpm install --frozen-lockfile && pnpm exec prisma generate && node -e "...http server..."
MOUNT bind   /home/alim/sandbox/deploy-src/lenzora-workspace-a655c1f7126dc1 -> /workspace
MOUNT volume sandbox-lenzora-workspace-a655c1f7126dc1_lenzora-sandbox-node-modules -> /workspace/node_modules
```

- No `node`/`pnpm` on the host PATH; pnpm only ever runs inside these containers.
- No host `~/.npmrc`. Lenzora's committed `.npmrc` sets `node-linker=hoisted` and
  **no `store-dir`**.
- Sandbox never writes an `.npmrc`, a store dir, or `node-linker` — only
  `sandbox/config/secrets.py:68` (classifies `.npmrc` as secret-bearing) and
  `sandbox/runtimes/presets/astro.py:24-25` (pnpm detection).
- The `*_lenzora-sandbox-node-modules` volume is declared in **Lenzora's own compose file**.
  Sandbox only supplies the Compose project name `sandbox-<runtime_id>`
  (`sandbox/runtimes/compose.py:151`), and `runtime_id` derives from the directory basename
  (`compose.py:44-51`). **Every new `-workspace-<hash>` directory therefore mints a brand-new,
  empty node_modules volume** — that is the 17 x ~1.49 GB = 25.4 GB.
- `/workspace/node_modules` being a *separate mount* means pnpm cannot place its store on
  the "same drive" as node_modules, so it falls back to a project-local store and writes
  `/workspace/.pnpm-store` — which lands on the host bind as
  `deploy-src/<ws>/.pnpm-store`. That is the 44.5 GiB.
- Even colocating them on the same *filesystem* is not enough: Linux `do_linkat` returns
  `EXDEV` when the two paths are on different **mounts**, not merely different filesystems.
  So while node_modules is its own volume mount, pnpm must copy. (Reasoned from kernel
  behaviour, **not** empirically tested — testing would have required writing on the host.)
  All host paths are on the same device (`stat -c %d` = 2049 for `/home/alim`,
  `/var/lib/docker`, `/var/lib/containerd`, `/tmp`).
- `node-linker=hoisted` is a **secondary** factor. Hoisted still hardlinks from the store
  when it can. The cross-mount split is the actual cause.
- **BuildKit is already correct.** `Dockerfile:20` and `Dockerfile.replay:12` use
  `--mount=type=cache,id=pnpm-store,target=/pnpm/store`, and `docker buildx du --verbose`
  shows exactly **one** `cached mount /pnpm/store` record at 1.524 GB. Build-time pnpm is not
  the problem; runtime `pnpm install` in the test container is.
- uid constraint: the test container runs as **root**. A shared store on a host bind would
  become root-owned, which breaks the `cp -a` workspace prep that runs as `alim`. Either run
  the service with `--user $(id -u)` or keep the shared store in a docker volume that is
  the *same mount* as node_modules.

**Fix**: mount the *parent* `deploy-src` (or a dedicated shared root) into the container,
drop the per-workspace `node_modules` volume so node_modules is a plain dir inside that
bind, and set `store-dir` to a sibling path inside the same mount. Then store and
node_modules share one mount, hardlinks work, and one store serves every workspace.
Estimated saving: **~41 GiB** (44.5 -> ~3.5) plus **~25 GiB** of volumes = **~66 GiB**.
Requires a Lenzora compose change AND a Sandbox bind-root change; spec-worthy.

## 4. Root cause B — workspaces are full `cp -a` copies, `.git` included (19.2 GiB)

- Deploy is `git push` over ssh into a **non-bare** repo with
  `receive.denyCurrentBranch=updateInstead` (`sandbox/core/_remote.py:620-640`, push at
  `:1028-1108`), then `cd T && git reset --hard <sha> && git clean -fd` (`:1125`), then a
  `tar -czf -` stream for the uncommitted layer (`:1275-1289`). Verified on the host:
  `git config --get-regexp` on `deploy-src/lenzora` returns
  `core.bare false` / `receive.denycurrentbranch updateInstead`.
- Workspace creation is **`cp -a "$source/." "$workspace"`**
  (`sandbox/transports/remote_jobs.py:96-134`) — not clone, not worktree, not rsync. The
  whole `.git` is byte-copied.
- Remote repos have **no git remote configured** (`git remote -v` empty on `lenzora`,
  `speckit-upstream-sync`, `sandbox`, and their workspaces). There is **no push-back path**
  anywhere (`sandbox/commands/deploy.py:20-23`; `docs/remote-hosting.md:131`;
  `specs/033-agent-aware-remote-sync/prd.md:36,281`). The only remote->local channel is job
  artifacts (`sandbox/commands/jobs_runtime.py:73-118`).
- The differing HEAD I saw in `lenzora-workspace-a655c1f7126dc1` (`5f4b544e5`) vs base
  `lenzora` (`a830a5436`) is explained by the workspace being a point-in-time `cp -a` of the
  base — it carries whatever HEAD the base had at copy time. **NEEDS-CHECK**: I did not diff
  workspace `git log` against the base reflog to *prove* no commit was authored on the
  remote. Every checkout also shows 4-114 dirty files, consistent with the tar-applied
  uncommitted layer.

### What on the remote actually needs git

| Consumer | Needs git in | Failure without `.git` |
|---|---|---|
| `_remote.py:1125` `git reset --hard` + push receive | base target only | hard failure, deploy aborts |
| `commands/hosting.py:391-395` | `deploy-src/hosts/*` | same |
| `sandbox_core.py:51` `ROOT_MARKERS` includes `.git` | **workspace** | project-root discovery walks out of the workspace and fails |
| `core/_instances.py:355-377` `symbolic-ref`, `rev-parse --git-common-dir` | **workspace** | derived instance name silently changes |
| `commands/zip.py:82-140` build stamp | workspace | degrades to `unstamped`, silent |
| `core/_hosting.py:161-178` `rev-parse --show-toplevel` | workspace | `source_root` manifests rejected |
| `recovery/git.py:65-108` | workspace | fails closed, no recovery bundle |
| `recovery/inventory.py:93-95` | hosts | degrades with a warning |

So a **workspace** needs `.git` to *exist* but needs **no history**. Base targets need real
git.

### Dedup options, with bytes

| Option | Saving | Risk |
|---|---|---|
| **`cp -al` (hardlink) for `.git` only**, `cp -a` for the worktree — one line at `remote_jobs.py:96-134` | **~14.6 GiB** (19.2 total - ~4.6 unique base) | Low. `git reset --hard`/`clean`/`tar -x` all replace inodes rather than write in place, so the base stays safe. A build writing in place *inside* `.git` would break sharing — hence `.git` only. |
| `git clone --local` per workspace (hardlinks `.git/objects`) | ~14.6 GiB | Low; safe against base deletion (refcounts). Bigger change than `cp -al`. |
| `git clone --reference` / alternates | ~14.6 GiB | Higher: workspace is not self-contained; deleting the base corrupts every child. |
| `git worktree` | ~14.6 GiB | Medium-high: cannot check out the same branch twice, and `--git-common-dir` resolution changes derived instance names (`_instances.py:373-377`). |
| `git clone --depth 1` | ~19 GiB | Loses history; `zip.py` `rev-list --count` build stamp degrades; redeploy `reset --hard <sha>` may miss objects. |
| **CoW reflinks** | 0 | **Not available.** `df -hT` shows `/dev/sda1 ext4`; ext4 has no `FICLONE`, so `cp --reflink=auto` silently full-copies. Would need XFS(reflink=1) or btrfs. |
| Drop git entirely, rsync | ~4.6 GiB | **Not recommended.** Breaks `_remote.py:1125`, root discovery, instance naming, recovery, and the `pushed_sha`-based deploy identity digest (`_remote.py:160-171`). Cost >> saving. |

## 5. No workspace retention exists

`sandbox/resources/adapters.py:683-726` classifies purely on the `-workspace-` substring +
live container mount + protections; an unresolvable owner *downgrades*
`stale_candidate -> unverified` so it is never auto-reclaimed. Age is computed
(`adapters.py:474`) but **never used as a reclaim predicate**. The only day-based retention
in the codebase is for jobs (`--retention-days` default 7,
`sandbox/commands/jobs_runtime.py:252-253`; sweep at `job_service.py:733-758`).

Recommendation: mirror the jobs retention for workspaces — reclaim a `-workspace-` dir when
(a) no running **or stopped** container binds its path, (b) no active `workspace_bindings`
row, (c) mtime older than TTL. Default 3 days for workspaces, 7 for base targets.

## 6. `sb` gaps found (for whoever owns the tooling)

- `./sb workspace list --remote scaleway-sandbox --json` ->
  `{"error":{"code":"workspace_index_incomplete", ...},"ok":false}`. Emitted whenever any
  record's status is in `{unresolved, conflict, incomplete, invalid, indeterminate}`
  (`sandbox/application/workspace_service.py:33`, checked `:653`, `:664-676`). For `list`
  it is documented as non-fatal (`:658-660`) yet the JSON contract returns `ok:false` with no
  payload, so **no reclaim path can see the 176 directories**. This is the single blocker.
- `./sb resources status --scope cache` reports `unknown_bytes = 178.6 GB` with
  `measured_bytes 0` for kinds `worktree` (174), `volume` (70), `runtime` (72) — see the
  companion note.
- `sudo -n du` works on this host; `docker system df -v` takes >2 min under load.

## 7. Steady state

- Now: 185 GiB used / 8.4 GiB free.
- After the SAFE-NOW deletes (~31 GiB): ~154 GiB used.
- After NEEDS-CHECK tier (stopped + idle-live speckit workspaces + `restore`): ~104 GiB.
- After fix A (shared store, -66 GiB) + fix B (`cp -al` .git, -14.6 GiB) + a 3-day TTL:
  one lenzora store ~3.5 GiB + one `.git` per base ~4.6 GiB + ~30 concurrent workspaces of
  non-node_modules content (~180 MiB each ~= 5.4 GiB) + docker (~29 GiB) + system ~10 GiB
  = **~55-60 GiB used, ~130 GiB free**. Uncertainty +/- 15 GiB.
