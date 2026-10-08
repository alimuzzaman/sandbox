# Server-to-server migration runbook

This runbook moves every site on one Sandbox remote to another remote. It was written
from the 2026-10 move of `scaleway-sandbox` (Caddy front door) to `xcloud-london` (an
xCloud-managed Contabo VPS whose host nginx stays the front door; see "nginx front door"
in [remote-hosting.md](remote-hosting.md)). The scripts live in
[`tools/server-migration/`](../tools/server-migration/). Every script has `--help`,
prints its commands with `--dry-run`, and refuses any destructive step without
`--confirm`. On a terminal, `--confirm` also asks you to type `yes`; `--yes` skips that.

| Script | Phase | What it does |
|---|---|---|
| `backup-to-drive.sh` | 1 | Recovery set, or git bundle + gpg + rclone + verification |
| `prepare-host.sh` | 2 | sudoers, docker group, swap, ufw check, `sb remote add/provision` |
| `deploy-project.sh` | 3, 4 | plan, apply, durable job, bounded polling, retire hint |
| `retire-failed.sh` | 4 | `sb host retire-delivery` for a failed delivery |
| `stage-volume.sh` | 5a | Pre-stages a volume tar on the new server (source may be live) |
| `copy-volume.sh` | 5 | Final volume copy: fetch (source must be stopped), then extract |
| `pg-transfer.sh` | 5 | Streams pg_dump to the new server, restores, compares row counts |
| `cutover-compose-data.sh` | 5 | The whole data cutover for one compose project |
| `wait-and-cutover.sh` | 5 | Waits for the deploy job to succeed, then runs the cutover |
| `verify-site.sh` | 4-7 | Checks the health URL, status and revision header with bounded retries |

Conventions:

- Hosts are always arguments (`--old alim@OLD_IP --new alim@NEW_IP`). No script has a
  default host, IP, password or token.
- Remote docker runs as `sudo -n docker`. The old server's user was not in the docker
  group, and the new one has NOPASSWD sudo from phase 2. Set `MIGRATION_DOCKER=docker`
  to change it.
- Data moves directly from the old server to the new one: `ssh -A old '... | ssh new ...'`.
  The forwarded agent authorizes both hops, nothing passes through the Mac, and no key
  is left on either server. The old server must already trust the new server's host
  key: BatchMode never prompts. Run `ssh -A old ssh new true` once by hand to accept it.
  If the old server reaches the new one under a different name, use `--new-from-old`.
- Transfer files land under `~/migration/<compose-project>/` on the new server.
- Measured on the live move: a 1.4 GB storage tar took 26 s, and `pg_dump -Fc -Z 6`
  turned a 1.85 GB database into 285 MB in 45 s. The whole Lenzora dev cutover took
  3.5 minutes.

## Phase 1: back up first

Nothing moves until every site has a verified backup off both servers.

**Sandbox recovery sets.** `sb recovery create` reads the passphrase only from the
inherited `RECOVERY_PASSPHRASE`. The script runs it as the child of `./sb secrets run`,
so the value never enters your shell:

```sh
tools/server-migration/backup-to-drive.sh recovery --remote scaleway-sandbox \
  --profile lenzora-prod-storage --backup-id 20261008T0300Z-lenzora-prod \
  --secret-source SOURCE_ALIAS --confirm
```

That runs `sb recovery create ... --destination gdrive:hermes-full-recovery --confirm`
under `sb secrets run --key RECOVERY_PASSPHRASE --destination RECOVERY_PASSPHRASE`, then
`sb recovery verify`. Sets are stored under `gdrive:hermes-full-recovery/sets/`.

**Postgres needs a registered source binding first.** Write the owner-only descriptor
(see [hosted-data-recovery.md](hosted-data-recovery.md)), then:

```sh
./sb recovery data --postgres-operation observe  --remote R --profile P --request-id OBS_ID --json
./sb recovery data --postgres-operation register --remote R --profile P --source-binding SOURCE.json --json
./sb recovery data --postgres-operation register --remote R --profile P --source-binding SOURCE.json --confirm --json
```

**Repositories with no runtime** (nothing deployed, so there is no recovery profile):

```sh
tools/server-migration/backup-to-drive.sh repo --repo ~/Sites/git/templately-astro \
  --name templately-astro --secret-source SOURCE_ALIAS --confirm
```

1. `git bundle create NAME.bundle --all`, then `git bundle verify`.
2. Under `sb secrets run`, the script re-invokes itself with `--internal-crypt`. That
   child encrypts with `gpg --symmetric --cipher-algo AES256`, passing the passphrase on
   gpg's stdin (never argv), decrypts again, and prints only the sha256 of the result.
3. The parent compares that sha256 with the bundle's. A mismatch stops the backup.
4. `SHA256SUMS` (plaintext and ciphertext hashes) and `NAME.bundle.gpg` go to
   `gdrive:hermes-full-recovery/manual/<UTC-stamp>-<name>/` with `rclone copy`.
5. `rclone check --one-way` compares the upload by hash (md5 on Drive).
6. The local work directory, including the plaintext bundle, is removed.

If the secret source alias is not registered, stop and have it registered. Never read
the secrets file instead.

## Phase 2: prepare the new server (once, needs root once)

```sh
tools/server-migration/prepare-host.sh --root-ssh root@NEW_IP --user alim \
  --remote-name xcloud-london --ssh-url alim@NEW_IP \
  --control-host sb-xcloud-london.example.com --confirm
```

On the server it:

- writes `/etc/sudoers.d/90-sandbox-<user>` (NOPASSWD), checking it with `visudo -cf`
  before installing it with mode 0440;
- adds the user to the `docker` group (the user must log in again);
- creates `/swapfile-sandbox` (8G by default, `--swap-size`), adds it to `/etc/fstab`,
  and writes `vm.swappiness=10` to `/etc/sysctl.d/90-sandbox-swap.conf`. A panel-owned
  `/swapfile` (xCloud ships a 1G one) is never touched;
- reports whether ufw allows 80/tcp and 443/tcp (`--open-ufw` adds the rules). The
  check matches port rules only; a rule by application profile name ("Nginx Full")
  reports as missing, so read the printed `ufw status` yourself.

Then, locally:

```sh
./sb remote add xcloud-london alim@NEW_IP --front-door nginx
./sb remote provision xcloud-london --control https \
  --control-host sb-xcloud-london.example.com --front-door nginx --confirm
./sb remote edge xcloud-london
```

The control host must be a **DNS-only** (grey cloud) A record that points at the new IP.
When the record is proxied, Cloudflare blocks the control client with **403, error
1010**. An unauthenticated request to the control URL returns 401, which is expected.

## Phase 3: runtime revision

`sb host apply` refuses with **`remote_runtime_revision_mismatch`** whenever the local
Sandbox checkout's commit differs from the remote runtime, which happens after every
local commit. Fix:

```sh
./sb remote up xcloud-london --confirm     # positional name; --remote is rejected
```

`remote up` refuses with **`remote_registration_busy`** while any `sb host apply` holds
`~/sandbox/runtime/remote-registration/registry.lock`. That is one global lock, and an
apply to a different remote holds it too. Find the holder with `lsof` on that file
and wait for it. Never kill another team's deploy. `deploy-project.sh --fix-runtime` runs
`remote up` and retries every 60 s for up to `--busy-timeout` (default 1800 s).

## Phase 4: deploy each project to the new remote

1. Use a clean worktree on a branch the environment allows. If the branch is checked
   out elsewhere: `git worktree add -f <dir> <branch>`. A dirty checkout is refused.
2. Make sure every compose service has a Docker healthcheck. Readiness checks the health
   of every service; a service without one is `unverified`, and apply fails with
   "remote runtime source/topology/health did not become fully ready before deadline".
3. Deploy:

```sh
tools/server-migration/deploy-project.sh --project-dir ~/Sites/git/lenzora-xcloud-dev \
  --environment development --remote xcloud-london --job-timeout 5400 --confirm
```

What happens:

- `sb host plan`, then `sb host apply --confirm`.
- Apply refuses with **`recovery_context_required`** and prints
  `recovery_context_required; prepare with: sb job-start --local ... --request-id ...
  --source-commit ... --timeout 900 -- sb host apply ...`. The script splits that
  command with `shlex` (no `eval`), checks it wraps `sb host apply`, raises `--timeout`
  to `--job-timeout`, and runs it. Lenzora needed 5400 s, mostly for the image build.
- `caffeinate -i -t <poll budget>` keeps the Mac awake. A sleeping controller strands
  the delivery record.
- `sb job-status <id> --json` is polled every `--poll-interval` seconds for at most
  `--poll-timeout` seconds. Exit 0 means the job succeeded, 1 means it failed (the script
  prints the last 200 lines of output and the retire command), and 3 means the budget
  ran out while the job keeps running.

Inside the job, apply builds, runs initializers, checks Docker health of every service,
writes the nginx include `/etc/nginx/conf.d/sandbox-host-<project>-<env>.conf`, upserts
the Cloudflare DNS records to the new IP, and verifies the public URL. If any step
fails, it rolls back the DNS records and the nginx include. While the new server builds,
the old one keeps serving.

Proxy and TLS rule for the hosting manifest:

| Need | Manifest |
|---|---|
| `basic_auth.bypass_ips` | `proxied: true` and `tls: origin-ca` (DNS-only plus `acme` rejects bypass IPs) |
| Anything else, DNS-only | `proxied: false` and `tls: acme` (certbot webroot through nginx) |

DNS-only records get a 60 s TTL. Hosted secrets, such as basic-auth passwords, resolve
from the controller's local secret store, so nothing is copied between servers.

**After a failed attempt** the next apply refuses with **`required_evidence_missing`**
or **`operation_busy`**. Read the job output, then:

```sh
tools/server-migration/retire-failed.sh --project-dir D --environment E \
  --remote xcloud-london --job-id <job> --confirm
```

It reads the `request_id` from `sb job-status <job> --json`, refuses while the job has
not ended, and runs `sb host retire-delivery ... --original-request-id <req> --confirm
--json`. A successful retire also voids any revision the attempt never observed
running, so the next apply of the same commit does a full recreate instead of refusing
with **`unproven_staged_revision`**. That includes an attempt that recorded its revision
and then failed at the edge: an environment without a declared source revision can never
prove its runtime, and before this fix it refused the same commit forever (the only way
out was a new commit). If retire itself refuses, the target stays fenced; for a
throwaway probe, use a new project name rather than fighting the fence.

**Long silent phases.** During the image build and the readiness wait, `job-status`
shows `suspected_stalled`. That is normal. Look at the remote directly before acting:
`tail ~/sandbox/runtime/hosts/<project>/<env>/apply.log` and `docker ps`. Do not cancel
by reflex: `sb job-cancel` can land after `compose_recreate`, which leaves the runtime
at the new revision while the ledger says cancelled.

**Wildcard DNS and a new hostname.** Under a `*.zone` wildcard record, macOS
mDNSResponder can keep a NEW hostname cached at the old wildcard IP for more than 5
minutes. Edge verification then fails with "TLS handshake failed". Do not resolve the
name before the deploy, or pre-create the record. Real sites with existing proxied
records are not affected. To test the new server regardless of local DNS, use
`verify-site.sh --resolve-ip NEW_IP`.

## Phase 5: data cutover for a stateful compose project

Example: Lenzora, compose project `sandbox-host-<project>-<env>`, Postgres service
`lenzora-db` (container `<compose>-lenzora-db-1`), file storage volume
`<compose>_lenzora-storage`, and in production a durable Redis job-queue volume
`<compose>_lenzora-production-job-queue-data` that holds pending jobs. The database user
and name come from the container's own `$POSTGRES_USER` and `$POSTGRES_DB`; they are
expanded inside the container and never printed.

**5a. Pre-stage (optional, any time before).** This measures the path and warms it:

```sh
tools/server-migration/stage-volume.sh --old alim@OLD_IP --new alim@NEW_IP \
  --volume sandbox-host-lenzora-production_lenzora-storage
```

**5b-5f. Cutover.** Start it the moment the deploy job SUCCEEDS, not before.
`sb host apply` ends by verifying the public URL on the new server. If the web stops
during that check, verification fails and apply rolls DNS back. So start the deploy job,
then let `wait-and-cutover.sh` wait for it:

The cutover stops the NEW containers before the OLD ones. By then DNS already points at
the new server, which is serving a fresh, near-empty database, so a few minutes of
errors is the better failure. Lenzora production took about 2 minutes this way.

```sh
tools/server-migration/wait-and-cutover.sh --job-id <job> --poll-timeout 7200 --confirm -- \
  --old alim@OLD_IP --new alim@NEW_IP \
  --compose-project sandbox-host-lenzora-production --db-service lenzora-db \
  --volume lenzora-storage --volume lenzora-production-job-queue-data \
  --volume lenzora-job-runtime-readiness \
  --health-url https://lenzora.example.com/api/health \
  --revision-header x-lenzora-revision --expect-revision <sha> \
  --check-table users --check-table projects
```

The script checks `--confirm` first, polls `sb job-status` with a bound, and runs
`cutover-compose-data.sh` only on `lifecycle=succeeded`. If the job fails or the budget
runs out, no data is touched. To run the cutover directly, use the same arguments with
`cutover-compose-data.sh`.

The cutover, in order:

1. Preflight: the Postgres container runs on both servers.
2. New server: stop every running container of the compose project except the database
   and any `--keep-service`, and record the names in `~/migration/<compose>/stopped.txt`.
   Step 7 restarts exactly that list. Use `--service` instead to stop only named services.
3. Old server: stop the same selection. Names are recorded in
   `~/migration/<compose>/stopped-old.txt` (on the old server) for rollback. A re-run
   merges the lists and never truncates them, so re-running after a partial failure is
   safe.
4. Final dump: `docker exec <db> sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z 6'`
   is piped through `ssh new` into `db.dump.partial`, which is renamed only after the
   whole stream has arrived.
5. Final volume tars (`tar --numeric-owner -cf - .` of each volume's `_data`), streamed
   the same way. Each copy first refuses if a running container uses that volume,
   because a final copy must be quiescent. That is why a Redis queue volume is copied
   with Redis stopped. Do not put such a service under `--keep-service`. Do not list the
   Postgres data volume either; the database moves by dump.
6. New server: `pg_restore -l` checks the archive. The default `--restore-mode recreate`
   (what the live cutover used) runs `dropdb --force` and `createdb -O "$POSTGRES_USER"`,
   then `pg_restore --no-owner --no-acl --exit-on-error --single-transaction`.
   `--restore-mode clean` uses `pg_restore --clean --if-exists` instead. Each volume is
   checked (`tar -tf`), refused if a running container uses it, emptied, and extracted
   with `tar --numeric-owner -xpf` so ownership and modes survive.
7. Start the recorded containers on the new server.
8. Verify: the health URL returns 200 (bounded retries) with the expected revision
   header, and `SELECT count(*)` matches for each `--check-table` on both servers.

The old server's stopped containers and data stay in place for the rollback window.
If any step after step 2 fails, the script prints the rollback commands.

For Lenzora dev the result was 307 tables with matching spot counts, `/api/health` 200
with `x-lenzora-revision`, basic auth 401 from a non-bypass IP, the bypass path 200, and
all 18 containers healthy.

The pieces also run on their own: `pg-transfer.sh --dump-only | --restore-only |
--check-only` and `copy-volume.sh --fetch-only | --extract-only`.

### Read-only maintenance mode (`--maintenance-service`)

An app that ships a read-only maintenance CLI can stay readable during the cutover
instead of losing writes. Lenzora has one (spec 056, `pnpm maintenance:status|enable|
drain|disable`, documented in its `docs/runbooks/hosted-production-images.md`). Pass the
service whose container runs it:

```sh
... --maintenance-service lenzora-monitor-worker [--maintenance-timeout 15] \
    [--maintenance-reason "server migration"] [--maintenance-old-only]
```

The script runs the CLI with `docker exec` in the first running container of that
service (`--maintenance-command`, default `pnpm --silent maintenance:`, is the prefix the
verb is appended to). Before step 2 it:

1. runs `status` on both servers, so a missing CLI stops the cutover before any change;
2. puts NEW into `read_only` with `enable`. DNS already points at NEW, and NEW still has
   its pre-seed; read-only means it serves those reads and refuses writes with 503 and
   `Retry-After`, instead of accepting writes the restore would silently discard.
   `--maintenance-old-only` skips this;
3. runs `drain --wait --timeout <min>` on OLD: writes are refused at once, queued work
   finishes, then the mode becomes `read_only`. A drain lasts at least 5.5 minutes, so
   the timeout must be at least 6. Then `status --json` must report `read_only`.

Steps 2 to 8 then run as above. The flag is stored in Postgres and bound to the
cluster's system identifier, so the restored copy of OLD's flag reads as `read_write` on
NEW's cluster; step 8 checks `status --json` on NEW for `read_write`. If the drain times
out (exit 4, OLD stays `draining`) or anything else fails before a container stops, the
script prints the `disable` commands for both servers. OLD keeps `read_only` for the
rollback window, and the rollback commands include `disable`. Run `disable` before any
migration on OLD: `prisma migrate deploy` refuses while the mode is not `read_write`.
GitHub webhook deliveries refused during the window need a manual redelivery afterwards.

## Phase 6: stateless sites

Phase 4 only, then `verify-site.sh --url https://site/ --expect-status 200`.

## Phase 7: WordPress (ASB)

There is no dedicated script for this phase yet.

1. Seed the new server from the Drive recovery set (`database.sql` + `wordpress.tar`;
   for ASB, set `20261007T154500Z-amarsonar-full`, staged at `~/migration/asb/`).
2. Deploy as in phase 4.
3. At cutover, freeze writes on the old site (maintenance mode or stop the web
   container), then copy a fresh database dump and the uploads the same direct way:
   the dump with `docker exec <db> sh -c 'mysqldump ...' | ssh new 'cat > ...'`, and the
   uploads with `copy-volume.sh` if they live in a volume, or with
   `ssh -A old 'sudo -n tar --numeric-owner -C <uploads> -cf - . | ssh new ...'` if they
   are a bind path.
4. Import, run the search-replace for any changed domain, and verify every site of the
   multisite network.

## Phase 8: decommission the old server

Only after every site is verified on the new server and a rollback window has passed.
The control endpoint and Hermes move last. Projects that are not migrated (for
example templately-astro) must have a Drive backup from phase 1 first.

## Per-site checklist

- [ ] Phase 1 backup exists and verifies (recovery set, or bundle with round-trip and
      `rclone check`)
- [ ] Every compose service has a healthcheck
- [ ] Clean worktree on an allowed branch; local Sandbox commit equals the remote runtime
      (`sb remote up` if not)
- [ ] Manifest proxy/TLS pair is valid (`bypass_ips` means `proxied: true` + `origin-ca`)
- [ ] New hostname not resolved locally before the deploy (wildcard zones)
- [ ] Optional: volumes pre-staged with `stage-volume.sh`
- [ ] `deploy-project.sh` succeeded; `sb job-status <id>` says `succeeded`
- [ ] Stateful: `wait-and-cutover.sh` (or the cutover) finished, row counts match, every
      durable volume (storage, job queue) copied
- [ ] `verify-site.sh` passes: status, revision header, basic auth where used
- [ ] `sb remote edge xcloud-london` shows the route loaded with no conflicts
- [ ] Old server's containers stopped but kept for the rollback window

## Rollback

During the rollback window the old server still holds stopped containers and their data.

1. Re-apply the project on the OLD remote:
   `deploy-project.sh --project-dir D --environment E --remote scaleway-sandbox --confirm`.
   This moves the DNS records back to the old IP.
2. Restart the old containers: `ssh old 'xargs sudo -n docker start < ~/migration/<compose>/stopped-old.txt'`.
3. Any writes made on the new server after the cutover are not on the old one. Copy them
   back the same way (swap `--old` and `--new` in `pg-transfer.sh`/`copy-volume.sh`)
   before step 2 if they matter.

A failed `sb host apply` already rolls back its own DNS change and nginx include, so a
failed deploy needs no manual rollback, only `retire-failed.sh`.

## Error strings and fixes

| Message | Cause | Fix |
|---|---|---|
| `remote_runtime_revision_mismatch` | Local Sandbox commit differs from the remote runtime | `./sb remote up <name> --confirm` (positional; `--remote` is rejected) |
| `remote_registration_busy` | Any host apply, to any remote, holds `registry.lock` | `lsof` the lock, wait for the holder; never kill it |
| `recovery_context_required; prepare with: ...` | apply must run inside a durable job | Run the printed `sb job-start` with a raised `--timeout` (`deploy-project.sh` does this) |
| `required_evidence_missing`, `operation_busy` | A failed delivery is still recorded | `retire-failed.sh --job-id <job> --confirm` |
| `unproven_staged_revision` | Failed attempt not retired, or retire refused (or Sandbox older than the fix for staged == recorded) | Run `retire-failed.sh`; if retire refuses, investigate with `./sb delivery inspect` (new project name for probes) |
| "did not become fully ready before deadline", `unverified` | A compose service has no healthcheck | Add a healthcheck to every service |
| "TLS handshake failed" during edge verification | macOS cached a new name at the old wildcard IP | Don't resolve before deploy, or pre-create the record |
| 403 error 1010 from the control host | Control record is proxied | Make it DNS-only |
| `basic_auth.bypass_ips` rejected | Bypass IPs need the real client IP | `proxied: true` + `tls: origin-ca` |
| `suspected_stalled` | Long silent build or readiness phase | Read `apply.log` and `docker ps` on the remote; do not cancel |
| Job `cancelled` but the site runs the new revision | Cancel landed after `compose_recreate` | Treat the runtime as deployed; re-apply to reconcile the ledger |
| Job stranded, controller asleep | Mac slept | `caffeinate -i -t <seconds>` (the scripts start it) |
| `refusing: running containers use <volume>` | Final copy of a live volume | Stop its service (do not `--keep-service` it) |
| ssh `Host key verification failed` on the nested hop | Old server never saw the new host key | Run `ssh -A old ssh new true` once and accept it |
| `ps` fails from an agent shell | Broken shim in the agent sandbox | Use `/bin/ps` |
| `No such image: <project>-<service>:latest` at `compose_recreate` | A `depends_on` service had no image on a fresh host (Sandbox before 88af8cf built only the declared services) | Use a Sandbox at or after 88af8cf (`build --with-dependencies`) |
| `delivery_terminal_conflict` from retire | That request was already retired; the stale staged revision survived it (Sandbox before 66d35ee) | Let the next apply refuse, then retire that apply's request id |
| Database container restarting: `/run/secrets/<name>: No such file` | A failed apply's rollback removed the runtime secrets | Nothing to fix by hand; the next apply writes them again. Do not pre-seed data until then |
