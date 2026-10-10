# xCloud server monitoring and provisioning review (xcloud-london, 2026-10-10)

Read-only review of what xCloud installs on a server it manages. It is the
evidence behind `specs/065-remote-server-health-monitoring/prd.md`. All
observations come from xcloud-london (Ubuntu 24.04, provisioned by xCloud),
gathered with read-only commands on 2026-10-10. Credential material is
described, never copied. That covers the per-server callback token in the
monitor and provisioning URLs, the SSH keys and the password hashes.

## What xCloud runs

| Artifact | Schedule / owner | Purpose |
|---|---|---|
| `/etc/cron.d/server-monitoring` → `/home/xcloud/.xcloud-monitoring/monitor.xc` | hourly (`*/60`), root | Collects host metrics and posts JSON to xCloud |
| `monitor.xc` | `shc`-compiled bash; the plain source is in the provisioning run log `/root/.xcloud/<task>.sh` | The binary is obfuscated; we did not decode it |
| `/etc/cron.d/xcloud-cleanup` → `/root/xcloud-cleanup.sh` | daily 18:59, root | Log and temp retention |
| sysstat (`debian-sa1`, `sysstat-collect.timer`) | every 10 min, root | Kernel-level history: `/var/log/sysstat/saDD`, 7 days, world-readable |
| `/etc/ssh/sshd_config.d/00-xcloud.conf` | provisioning | `PasswordAuthentication no`, `PermitRootLogin prohibit-password` |
| `/etc/modprobe.d/xcloud-*.conf` | provisioning, re-applied after upgrades | Blacklists `esp4/esp6/rxrpc`, `act_pedit` and `algif_aead` as kernel CVE mitigations |
| `/etc/nginx/modules-enabled/50-xcloud-modules.conf`, `/etc/apt/apt.conf.d/52xcloud-nginx-modules` | provisioning | nginx.org build plus headers-more and brotli modules; nginx blacklisted from unattended-upgrades |
| `/etc/docker/daemon.json` (written only if absent) | provisioning | json-file logs capped at 10 MB × 3 per container |

The xCloud monitoring directory is not readable by the Sandbox remote user
without sudo. The Sandbox user (`alim`) is in the `docker` and `sudo` groups
and has passwordless sudo on this host.

## How the monitor collects each dashboard field

The monitor takes two arguments: `nocallback`, which prints the JSON instead
of posting it, and CPU and memory alert thresholds.

| Field | Technique | Keep / change for Sandbox |
|---|---|---|
| CPU % | Two `/proc/stat` reads 10 s apart, taken **first**, before any other check, so the collector's own load is not counted; value is 100 − idle%, with iowait counted as busy | Keep the ordering and the iowait rule; use a shorter window (1-2 s) for interactive snapshots and let sysstat cover history |
| Load | `/proc/loadavg` 1/5/15 | Keep; also report it relative to `nproc` |
| Memory, swap | `free -m` | Prefer `/proc/meminfo`, which needs no parsing of localised output |
| CPU MHz, cores, uptime | `/proc/cpuinfo` with fallbacks, `nproc`, `uptime -p` | Keep |
| Disks | `df -H -P -x tmpfs …`, filtering out overlay2, `/snap` and `/run`; "usable" = `df -m /var/www` | Keep the filters; replace `/var/www` with the volume holding `$SANDBOX_HOME` and the Docker root |
| Per-site stats | Users with uid ≥ 1000 that own `/var/www/*`; per-user CPU and RSS summed from `ps`; `du` of the site dir **plus** the named volumes of the site's Compose project (project found via the `com.docker.compose.project.working_dir` label, volumes via `docker volume ls --filter label=com.docker.compose.project=…`) | Adopt the volume-inclusive disk attribution; map Sandbox owners from registries and Compose labels, not from Unix users |
| Upgradable packages | `apt list --upgradable`, with nginx removed when its source is nginx.org; waits on dpkg locks for up to 300 s | Keep; split out security updates; mark held packages; drop the wait to a short bound for interactive use |
| Top CPU | `HOME=/nonexistent LC_ALL=C top -bcn2 -d 2 -o %CPU -w 512`, second iteration, divided by core count; command JSON-escaped and capped at 200 chars | Keep (the C locale and empty HOME stop user toprc and locale from changing the output); also redact secrets in arguments |
| Top RAM | `ps -eo pid,user:20,comm,rss --sort=-rss` | Keep |
| Containers | `docker ps --format '{{json .}}'` plus `docker stats --no-stream` per container | Use a single `docker stats --no-stream` call for all containers; it is cheaper |
| Reboot required | `/var/run/reboot-required` (and `.pkgs`) | Keep |
| Services | `systemctl is-active` for nginx, ssh, supervisor and docker | Keep; derive the list from what Sandbox depends on (docker, the front door, the control service, the cleanup timer) |
| Self-heal | Restarts ssh if it is inactive and posts an "ssh reboot" callback | Do not copy; Sandbox reports only |
| Log | Appends "Callback sent at" to `monitor.log` only when ≥ 10 MB is free | Keep the low-disk write guard |
| Transport | `curl --insecure` POST to an xCloud API URL whose path contains the per-server token | Anti-pattern: no TLS verification on an authenticated channel, and the secret is in the URL (it ends up in logs and process lists) |

The dashboard's "Live Monitoring (every 5 s)" view polls a lighter path while
it is open. Its "Server Statistics (24 h)" graph is built from the hourly
posts. sysstat's 10-minute samples on the host are finer than xCloud's own
history.

## Cleanup script (`xcloud-cleanup.sh` v1.6.0)

- Selects home directories from `UID_MIN`/`UID_MAX` in `/etc/login.defs`
  rather than hardcoding them, and deletes files older than 30 days from each
  `~/.xcloud`.
- Applies the same rules to `/var/log`, `/usr/local/lsws/logs` and every
  user's `~/.pm2/logs`:
  - rotated or compressed logs are deleted after 30 days;
  - live logs over 10 MB are cut to their last 10 MB. The cut is `tail` into
    a tmp file, then `cat` over the original. That keeps the writer's inode
    but is not atomic.
- `journalctl --vacuum-time=7d`, plus duplicity cache retention of 7 days.

Lessons for Sandbox:

- Apply age-based retention to Sandbox-owned logs and job output on remotes.
  This is related to the TODO "inventory what files a tmp remote instance
  creates".
- Keep size caps on live logs.
- Never truncate logs Sandbox does not own.

## Provisioning practices worth adopting (from the line-range reviews)

- Every network or package step is bounded with `timeout -k <grace> <limit>`:
  `dpkg --configure -a` 120 s, `apt update` 90 s, keyserver and curl 60 s.
  This matches CLAUDE.md gotcha 21.
- A bounded wait on dpkg/apt locks (300 s cap). One flaw: it returns success
  after the cap. Sandbox should report `unavailable: package_lock_busy`
  instead.
- `nginx -t` runs before every reload, and the script aborts with "reload
  aborted to prevent outage". Sandbox's nginx front door already does this.
- The Docker daemon log cap is written only when the file is absent, so
  operator edits survive.
- Post-condition checks after each stage, such as cert PEM markers and
  docker/compose versions, plus a hello-world smoke run that is cleaned up
  afterwards.
- Changes to package sources and keys are staged: write a temp file, verify
  it, then publish it, and roll back every artifact on failure.
- Kernel-module mitigations are checked with a dry run and re-applied after
  `apt-get upgrade`.
- Numbered progress checkpoints let the control plane name the last step
  reached when a run stalls.

## Practices to avoid

- `curl --insecure` on any authenticated callback, and secrets in URL paths.
- Obfuscated binaries (`shc`) run as root from cron. They cannot be reviewed
  and add no real secrecy, since the plain source stays in the run log.
- Self-healing actions (restarting ssh) inside a metrics collector.
- `apt-get --force-yes`, an unbounded `apt-get upgrade -y`, and restarting
  sshd without running `sshd -t` first.
- A UFW rule that opens SSH to all addresses after an IP allowlist, which
  defeats the allowlist (provisioning line ~973).
- Hourly-only sampling for a dashboard that claims a 24-hour history.

## Coexistence rules for Sandbox on xCloud hosts

1. Detect xCloud by the presence of `/etc/cron.d/server-monitoring` and of
   the `/home/xcloud/.xcloud-monitoring` directory. Never read the files
   inside it.
2. Never execute, modify, disable or re-schedule xCloud's monitor, cleanup
   or crons, and never call its API.
3. Reuse sysstat history; do not add a second sampler while sysstat is
   active.
4. Label xCloud-managed sites (uid ≥ 1000 owners of `/var/www/*`) as
   "other tenant", reported as aggregates.
5. Expect xCloud's daily cleanup to cut logs under `/var/log` to 10 MB.
   Sandbox must not depend on old log lines there.
