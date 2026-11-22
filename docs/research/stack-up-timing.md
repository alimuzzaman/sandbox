# Where `sb host stack up` spends its time

Measured 2026-11-22 on `xspeed-dev` (a joined Tailnet remote), using a scratch
stack `perf-1` from the xspeed-hub template `sandbox.full.hosting.yml`: 6
runtime services, 4 built images, 5 public names, ACME TLS, DNS-only records.
The controller was a Mac on release `5664fdd`, and the remote ran release
`cefcafa`.

## How a stack up runs on a joined remote

1. **Mac, `dispatch_server_cli`.** The Mac checks the target's identity and
   installed contract, pushes the template checkout to
   `deploy-src/<slug>`, resets and measures it under the materialization
   lease, copies it to a workspace and submits a `server-cli` job. Every step
   is a separate Tailnet SSH session. Tailnet SSH does not share connections
   (each command gets a fresh policy check), so each round trip costs 1.6 to
   5 s.
2. **Outer server job.** `host stack up` runs on the server: listing, port
   probe, preview, `--generate`, name claim, worktree, nested `host apply`
   submission.
3. **Nested apply.** Source staging, Compose build, recreate, runtime start,
   then health, observation, certificate, nginx route, DNS records, certbot,
   edge verification and the inbox name.

## Baseline

| Step | Fresh stack | Unchanged re-apply |
|---|---|---|
| Mac dispatch (first push of a new checkout) | 82 s, of which `git push` took 41 s | |
| Mac dispatch (checkout already pushed) | 41 s | 51 s |
| SSH round trips in the dispatch | 15 (`echo $SANDBOX_HOME` alone ran 5 times, 9 to 12 s) | 15 |
| Outer server job | 8 s | 8 s |
| Nested apply, start to `compose_build` | 30 s | |
| `compose_build` (all layers cached) | 3 s | |
| `compose_recreate` | 22 s | not run (edge-only replay) |
| After `runtime_start` | 69 s | |
| Nested apply total | 2 m 08 s | 47 s |

The 69 s after runtime start, from the server's file times and the certbot
log:

| Window | Took | What runs |
|---|---|---|
| 06:08:04 to 06:08:35 | 31 s | health, observation, first nginx route, 10 DNS records (about 40 Cloudflare calls at about 0.2 s each) |
| 06:08:35 to 06:08:43 | 8 s | certbot for the 5 names |
| 06:08:43 to 06:08:44 | 1 s | nginx reload with the certificate |
| 06:08:44 to 06:09:10 | 26 s | a fixed 20 s new-record DNS grace, then the 5 routes verified one after another |
| 06:09:10 to 06:09:13 | 3 s | inbox name and bookkeeping |

Compose build is already parallel. All four images were built in one BuildKit
session, and their steps interleave in the log. The Hub's `npm run build`
(27 s in the earlier `guided-return` run) is the floor when its layer changes.

## What changed

- **Phase markers.** `sandbox/hosting/phase_timing.py` adds a lap timer.
  - Mac dispatch: `[Sandbox] dispatch phase=…` on stderr for `identity`,
    `capabilities`, `source_upload`, `workspace` and `job_submit`.
  - Outer job: `[Sandbox] stack-up phase=…` on stderr for `template`,
    `listing`, `ports`, `preview`, `secrets` (`--generate`), `claim`,
    `worktree` and `submit`.
  - Nested apply: `[Sandbox] apply phase=…` in the job output for `secrets`,
    `source_artifact`, `admission`, `edge_preflight`, `source_push`,
    `source_reset`, `runtime_evidence`, `runtime_health`,
    `runtime_observation`, `edge_certificate`, `edge_route`, `edge_dns`,
    `edge_after_dns` (certbot), `edge_verify`, `edge_finalize`,
    `delivery_record` and `mail_name`.

  Each phase prints a `started` and a `finished … elapsed=Ns` line. A phase
  that raises keeps only its `started` line, which shows where the run stopped.
  Compose phases keep their markers in apply.log.
- **One home lookup per dispatch.** Inside `remote_home_cache()`, opened by
  `dispatch_server_cli`, `resolve_sandbox_home` asks each SSH endpoint once.
  Measured on a `--plan`: 5 lookups became 1, round trips went from 15 to 11,
  and the dispatch went from 41 to 51 s down to 32 s. The cache holds only the
  validated path, keyed by the exact SSH argv, and ends with the dispatch.
  Every other remote command still opens its own session.
- **Fewer Cloudflare calls.** `_ZoneLookup` asks each zone candidate once per
  apply; only a definite `zone_not_found` is remembered. A record that already
  holds exactly what `upsert_address` would write (type, content, proxied, TTL
  and the managed comment) gets no GET or PUT, and its rollback entry restores
  the same record. An unchanged re-apply of this template drops from about 40
  calls to about 16. A fresh apply drops from about 40 to about 36.
- **The DNS grace starts when the records are written.** `_verify_edge` takes
  `fresh_since`, which is when the apply's last DNS write finished. The
  certificate and route work since then counts toward the 20 s. Nothing on
  the server looks the names up in between: nginx does not resolve
  `server_name`, and certbot's HTTP-01 lookups happen at Let's Encrypt. The
  other `_verify_edge` callers pass no `fresh_since` and still wait the full
  20 s.
- **Routes are verified in parallel.** Each route's probe is read-only and
  independent, so up to 8 run at once. The first failure in declaration order
  is raised, as before. Set `_EDGE_VERIFY_WORKERS = 1` to go back to the
  serial loop.

Expected effect on a fresh stack of this template, not yet measured live:
about 10 s less dispatch, about 8 to 12 s less DNS grace (the grace now overlaps
certbot), and about 4 s less route verification. An unchanged re-apply also
saves about 5 s of Cloudflare calls. The server-side changes take effect once
the remote runs a release that includes them, and the new markers will then
give the real per-phase numbers.

## What did not change, and why

- **Digest and upload alongside the name claim and `--generate`.** On a joined
  remote these happen on different machines at different times. The Mac uploads
  first, and the claim and `--generate` run inside the server job (8 s in all).
  Starting the claim before the source is accepted would leave a claimed name
  for a job that might never be submitted.
- **The capability probe alongside the identity check.** The probe has to use
  the registration as it stands after the identity read ("a fresh identity
  read may have safely reconnected the observed revision cache"). Running both
  at once could check one registration and submit against another.
- **The four materialization-lease steps** (acquire, reset, measure, release:
  about 8 s). Each step leaves a durable receipt that `inspect` and `reconcile`
  depend on after a crash. Merging steps is a protocol change that needs its own
  design.
- **Skipping the source reset when nothing changed.** The reset and measure are
  the proof the job's source is exact. A skip would itself need a measure, which
  is most of the cost. The `git push` is already incremental: 2 s when unchanged,
  41 s only for the first push of a new checkout.
- **Recreating only changed services.** The apply uses `--force-recreate
  --always-recreate-deps --renew-anon-volumes` on purpose. A bind-mounted source
  change, an anonymous application volume or an initializer's evidence marker
  would not trigger Compose's own change detection. An unchanged re-apply
  already skips Compose entirely through the exact-runtime edge-only replay
  (47 s here, against 2 m 08 s fresh). A targeted mode should be an explicit
  manifest opt-in.
- **Per-service health checks in parallel.** There is no serial per-service
  loop to change. `up -d` waits on every `depends_on` health check itself, and
  the apply then makes one loopback check and one whole-runtime observation.
- **Front door, route and inbox name alongside the health checks.** The route
  must not point at a runtime that has not passed health and observation; that
  ordering is what rollback relies on. The inbox name is published only after
  the delivery succeeded, and takes about 2 s.
- **DNS writes in parallel.** The rollback replays `changes` in reverse
  order, and the drift and adoption checks read the shared record state. After
  the call cuts above, the remaining gain is about 5 s on a fresh apply.

## Reading the markers

```bash
sb job-output <outer-job> --remote R   # stack-up phases
sb job-output <apply-job> --remote R   # apply phases
grep "apply phase=" ~/sandbox/runtime/hosts/<project>/<env>/apply.log   # Compose phases
```
