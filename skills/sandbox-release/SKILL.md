---
name: sandbox-release
description: Publish a new release of the Sandbox tooling itself to the public install site at https://sandbox.xc1.app/. Builds the runtime tarball from HEAD, bakes the version + BASE_URL into the served install.sh + landing page, and deploys all three files into the live nginx container on the box. Use when the user says "release sandbox", "publish sandbox", "make a sandbox release", "ship the install site", or "deploy sandbox.xc1.app". This releases the SANDBOX itself, NOT any focused WordPress plugin.
---

# Sandbox release — publish the install site

Ship a new version of the **sandbox tooling** to the public one-line installer at
**https://sandbox.xc1.app/**. End users install with:

```bash
curl -fsSL https://sandbox.xc1.app/install.sh | sh
```

This is the sandbox-infra release flow. It is NOT a plugin release — for a
WordPress plugin (xspeed, embedpress, …) use that plugin's own dist/share-build
flow instead.

## ⛔ Gate — these are per-action approvals

`commit`, `push`, and `deploy to the live public site` each need the user's
explicit say-so for THAT action (per CLAUDE.md). Stage the work, show the diff,
and wait. A release is public and outward-facing — never auto-run it.

---

## The model (read once — these are the non-obvious bits)

- **The tarball is built from `git archive HEAD` — committed files ONLY.**
  Uncommitted / untracked changes do NOT ship. So the order is always
  **commit → push → build → deploy.** If you build before committing, the
  release silently omits your changes.
- **The version comes from `sandbox.yml` (`version: N`).** Both
  `scripts/make-release.sh` (tarball name) and `deploy/install-site/publish.sh`
  (the `v{{VERSION}}` badge baked into `index.html`) read it. Bump it there and
  everything stays in sync. Bump version ONLY at release.
- **The public box has no repo and no bind-mounts.** The running container
  `sandbox-install-site` (image `sandbox-install:latest`) serves files baked in
  at image-build time from `/usr/share/nginx/html/` — `docker inspect` shows
  `Mounts: []`. So a release is: build the files locally → `scp` to the box →
  `docker cp` them INTO the running container. No image rebuild, no restart.
- **`docker cp` is not durable across container recreation.** If the container
  is ever recreated from the old image, it reverts to the last baked-in build.
  To make a release survive recreation you must rebuild + redeploy the image
  (see "Durable deploy" below). For routine releases the `docker cp` path is
  what's used.
- **Cloudflare:** HTML is served `cf-cache-status: DYNAMIC` (not edge-cached) —
  the new page shows immediately. The `.tar.gz` caches `max-age=14400` (4h) at
  the edge; a brand-new tarball is a `MISS` and serves fresh. No purge needed in
  practice; bust HTML checks with `?v=$RANDOM`.

---

## SSH to the box

The public host is `45.63.18.41`. Use the SSH alias **`XSpeed-Nginx`** (user
`akash`, has passwordless sudo, docker access). The `root@` and `SpeedPress`
logins are denied — don't use them.

```bash
ssh XSpeed-Nginx 'docker ps --format "{{.Names}}" | grep install-site'
```

If `XSpeed-Nginx` ever stops resolving, the host blocks are in `~/.ssh/config`
(two aliases point at `45.63.18.41`; `XSpeed-Nginx` is the working one).

---

## Steps

### 1. Decide what's shipping + the version

```bash
cd /Applications/Workspace/GitHub/sandbox
git status && git log --oneline -5
grep -E '^version:' sandbox.yml
```

Confirm with the user whether this is a version bump. If yes, edit
`sandbox.yml` (`version: X.Y.Z`). Don't bump for nothing — only at release.

### 2. Commit + push (each needs approval)

Stage only the intended files. Exclude local-only / stray artifacts
(`.claude/settings.local.json`, stray `*.png` in root, anything under
`runtime/` that isn't gitignored). Then, after the user OKs:

```bash
git add <intended files>
git commit -m "<message>"   # no AI/Claude mentions
git push origin <branch>    # never push to main without explicit per-action OK
```

### 3. Build + stage the public files locally

`publish.sh` runs `make-release.sh` (builds the tarball from HEAD), bakes
`BASE_URL` into `install.sh`, and bakes the version into `index.html`:

```bash
SANDBOX_BASE_URL=https://sandbox.xc1.app ./deploy/install-site/publish.sh
```

Output lands in `deploy/install-site/public/`:
`index.html`, `install.sh`, `sandbox-latest.tar.gz`, `sandbox-<ver>.tar.gz`.

**Verify before deploying** that the build actually contains your changes and
the version is right:

```bash
cd deploy/install-site/public
grep -o 'class="ver">v[^<]*<' index.html                       # version badge
tar -xzOf sandbox-latest.tar.gz sandbox/<changed-file> | grep <marker>   # code is in the tarball
ls -1 *.tar.gz                                                  # name == version
```

### 4. Deploy into the live container (needs approval)

```bash
cd deploy/install-site/public
scp -q index.html install.sh sandbox-latest.tar.gz sandbox-<ver>.tar.gz XSpeed-Nginx:/tmp/
ssh XSpeed-Nginx 'set -e
  for f in index.html install.sh sandbox-latest.tar.gz sandbox-<ver>.tar.gz; do
    docker cp /tmp/$f sandbox-install-site:/usr/share/nginx/html/$f
  done
  docker exec sandbox-install-site sh -c "
    rm -f /usr/share/nginx/html/sandbox-<OLD-ver>.tar.gz;   # drop the previous pinned tarball
    chown root:root /usr/share/nginx/html/* ;
    chmod 755 /usr/share/nginx/html/install.sh"
  docker exec sandbox-install-site ls -lh /usr/share/nginx/html/
  rm -f /tmp/index.html /tmp/install.sh /tmp/sandbox-*.tar.gz'
```

### 5. Verify live (always — through Cloudflare)

```bash
curl -fsSL "https://sandbox.xc1.app/?v=$RANDOM" | grep -o 'class="ver">v[^<]*<'   # version badge live
curl -fsSL "https://sandbox.xc1.app/install.sh" | grep -m1 'BASE_URL='            # baked host
curl -fsI  "https://sandbox.xc1.app/sandbox-latest.tar.gz" | grep -iE 'HTTP/|content-length'
# prove the LIVE tarball has the new code:
curl -fsSL "https://sandbox.xc1.app/sandbox-latest.tar.gz" | tar -xzO sandbox/<changed-file> | grep <marker>
curl -fsI  "https://sandbox.xc1.app/sandbox-<OLD-ver>.tar.gz" | grep HTTP/   # expect 404 if you removed it
```

Done only when the live URL serves the new version + the live tarball contains
the new code. "Built locally" is not "released."

---

## Durable deploy (when the release must survive container recreation)

The routine flow above `docker cp`s into the running container, which reverts on
recreate. To bake the release into the image instead, rebuild
`sandbox-install:latest` from `deploy/install-image/` with the staged `public/`
files as its content, then `docker compose up -d` to recreate the container from
the new image. Do this when cutting a "real" pinned release or when the box may
be rebuilt. Confirm with the user — it recreates the container (brief blip).

---

## Common mistakes (already hit — don't redo)

- **Building before committing** → tarball ships stale code (`git archive HEAD`).
- **Using `root@` or `SpeedPress`** for SSH → permission denied. Use
  `XSpeed-Nginx`.
- **Looking for `publish.sh` on the box** → it isn't there; the box has no repo.
  Build locally, copy the files over.
- **Expecting an nginx restart to matter** → `docker cp` writes into the running
  container's fs; nginx serves the new file immediately, no restart.
- **Forgetting to drop the old pinned `sandbox-<old>.tar.gz`** → it 200s forever
  alongside the new one. Remove it in the deploy step.
- **Treating this as a plugin release** → it's not. Plugins ship via their own
  dist/share-build flow; this only touches the sandbox install site.
