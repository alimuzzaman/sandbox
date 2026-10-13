# Phase B: hard storage isolation on xcloud-london (plan only)

Phase A refuses submissions that would cross the floor and removes finished
workspaces, but an admitted run that outgrows its estimate, or build cache
growth, can still fill the one shared filesystem. Phase B makes the reserve
physical. **Nothing here runs without the user's explicit approval and a
maintenance window.** Each option lists downtime, rollback and the approval
point.

Host facts (2026-10-13): one 197 GB ext4 root (`/dev/sda2`), Docker data root
`/var/lib/docker` (overlay2) on it, ~108 GB free after cleanup; lenzora
production and development stacks plus amarsonar, xspeed CFE and
alimuzzaman-me hosted there.

## Option A: fixed-size filesystem for test workloads (recommended)

Test workspaces and their volumes live on a preallocated image; when it fills,
only tests fail.

1. Approval point 1: size (proposal 100 GB, leaving ~95 GB for the OS, images
   and hosted data) and window.
2. Allocation gate: freshly measured free space minus the full allocation must
   leave at least the 30 GB Phase A floor, plus an agreed growth margin. With
   the noted 108 GB free, a 100 GB allocation is refused. Reclaim approved test
   resources, choose a smaller allocation, or attach storage before proceeding.
   `fallocate -l 100G /srv/sandbox-test.img && mkfs.ext4 -m 0 /srv/sandbox-test.img`;
   fstab entry `loop,nofail` mounted at `/srv/sandbox-test`. No downtime.
3. Move `~/sandbox/deploy-src` workspace entries (not `hosts`, not hosted
   deploy sources) onto it with `rsync -aHAX`, then a symlink-free bind mount
   over `~/sandbox/deploy-src/workspaces` (code change: workspaces placed under
   that subdirectory; ships as its own spec slice with tests).
4. Test compose volumes: a Sandbox-owned local volume driver option
   (`type=none,o=bind,device=/srv/sandbox-test/volumes/<name>`) applied only to
   workspace projects (code change, same slice).
- **Downtime**: none for hosted sites; running test jobs must be drained (reap
  or wait) before step 3.
- **Rollback**: stop new submissions, rsync back, remove the fstab line and
  image. Hosted data never moves.
- **Risk**: loop device I/O overhead for tests (acceptable); the image itself
  consumes 100 GB immediately, so its creation must pass the allocation gate before any formatting or mounting.
  Build-cache GC does not prove that any specific number of bytes will be freed.

## Option B: move production data to its own reserved filesystem

1. Approval point 1: maintenance window with lenzora downtime (DB dump plus
   copy; estimate 15-30 min for current sizes).
2. Create a fixed image (e.g. 40 GB) for `lenzora-postgres-data`,
   `lenzora-storage` and related volumes; stop the production stack
   (`sb host` maintenance), copy volumes with `rsync -aHAX`, recreate them as
   bind-backed local volumes, start, verify health and a DB integrity check.
- **Downtime**: yes, production.
- **Rollback**: original volumes are kept untouched until the user approves
  their removal; revert = point compose back at them and restart.
- **Risk**: highest; touches production data. Only with explicit go-ahead.

## Option C: capped build cache (combine with A or B)

1. Approval point: daemon restart window (all containers restart unless
   `live-restore` is on; check first).
2. `/etc/docker/daemon.json`: `"builder": {"gc": {"enabled": true,
   "defaultKeepStorage": "10GB"}}`; validate with `dockerd --validate`, then
   restart Docker.
- **Downtime**: brief restart of every container (hosted sites included)
  unless `live-restore` is already enabled.
- **Rollback**: restore the previous `daemon.json` (backed up first) and
  restart.

## Recommendation

C in the first approved window, then A only after the measured allocation gate passes.
A cache cap bounds future retention; measure any recovered bytes rather than assuming 10 GB.
B only if hosted data growth itself becomes the risk.
