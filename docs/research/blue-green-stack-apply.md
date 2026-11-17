# Blue-green stack apply (side-loading)

Status: design for `feat/blue-green-stack-apply`, 2026-11-17.

## Problem

`sb host apply` (and `sb host stack up`, which runs it) rebuilds a target and then
recreates its whole Compose project in place (`compose_recreate`: `up
--force-recreate --always-recreate-deps`). From the first container stop until
the new edge answers, the target's hostnames fail. On xspeed-dev that is one to
three minutes for the full Hub + WordPress stack.

## Shape: side-load the new revision, keep the steady state

The steady state does not change: one Compose project
`sandbox-host-<project>-<env>` (the *base*), on its one loopback port P, with the
same service names, routes, panel records, logs, exec, destroy and reaper paths.
Only during an apply does a second project exist:

- **side project** `sandbox-hostside-<project>-<env>`: the new revision's
  *colored* services, on loopback port **S = P + 1000** (19000-19999: no
  Sandbox allocation uses that band; primaries are 18000-18999, gates P + 3000).
  `sandbox-hostside-` never starts a base name (`sandbox-host-` + a project that
  begins with a letter or digit), so it cannot collide with another target.

Apply, when the target is eligible (below):

1. **Build** every image, as today. Running containers are untouched.
2. **Render the side project** from the new revision's resolved model
   (`docker compose config --format json` with the base files, override and env
   file). The generated file, written 0600 next to the base override:
   - keeps only colored services; drops `depends_on` entries naming shared ones;
   - publishes only the primary service, on `127.0.0.1:S:<container_port>`;
   - maps every named volume to the base's volume (`external: true`,
     `name: <base>_<volume>`): data is shared, never copied;
   - uses one external network `sandbox-hostside-<project>-<env>_net`, which
     Sandbox creates and to which it connects each running shared container with
     its service name as alias, so `db`, `wp-db` and `mailpit` resolve exactly as
     in the base, and no base colored container is reachable from the side;
   - pins images: a service with `build` and no `image` uses the base's built
     tag `<base>-<service>`; the side starts with `--no-build --pull never`.
3. **Start the side** with `up -d --no-deps <colored services>`, under the same
   ingress locks and in-lock port recheck as the base `up` (owner: the side
   project, port S).
4. **Health-check S** with the manifest's health path and statuses.
   Failure: the side is removed, the network deleted, and the apply fails with
   `blue_green_side_unhealthy`. The base and the route were never touched, so the
   old revision keeps serving and nothing needs rolling back.
5. **Switch the route** to S. The installed route (Caddy fragment or nginx conf)
   is re-read, every `127.0.0.1:P` upstream becomes `127.0.0.1:S`, and the result
   goes through the existing edge adapter's `configure` (validate, reload, marker
   probe, restore on failure). Certificates, names and auth stay byte-identical;
   only the upstream number changes. From here, the rollback baseline is this
   side route, not the pre-apply route.
6. **Recreate the base** while the side serves: shared services first with a
   plain `up -d --no-deps` (Compose recreates one only when its config changed),
   then the colored services with `--force-recreate`. Then the existing health
   check on P and runtime observation.
7. **Switch back** to P: the normal route render/configure, DNS, edge
   verification of this apply.
8. **Remove the side**: `down` (no `-v`; its volumes are the base's), disconnect
   the shared containers, delete the network.

If step 6 or 7 fails, the route stays on the healthy side and the side keeps
running; the apply fails with the normal recorded failure. The next apply sees a
running side and a route on S, skips steps 2-5 (the side already serves), and
continues from step 6. `host destroy` removes a leftover side project and
network too.

## When migrations and init services run

- **Init services** (`compose.init_services`) run as Compose dependencies of the
  in-place recreate, with their own completion evidence. This version does not
  side-load them: a manifest that declares any applies in place (`auto`) or is
  refused (`blue-green`).
- **Start-time migrations and seeds** inside a colored service (the Hub's
  `seed-dev.mjs`) run when the side starts, against the shared data, while the
  old revision still serves, and again when the base recreates. They must be
  idempotent and backward compatible for one revision, or the template sets
  `deploy.strategy: in-place`.

## Shared vs colored services

A service is **shared** (one container, base only, never side-loaded) when it:

- is listed in `deploy.blue_green.shared` (every database and other stateful
  server: two Postgres or MariaDB servers on one data directory corrupt it);
- publishes a host port itself (Mailpit's loopback inbox);
- is the Sandbox login gate (`sandbox-login`, which holds share records on tmpfs).

Everything else is **colored**, and the primary service must be. Two colored
containers may mount one named volume (two Apache/PHP servers on one docroot are
fine). Without a `shared` list, `auto` side-loads only a stack whose colored
services mount no named volume; anything else applies in place.

Because shared services are not recreated per revision, the pushed-revision
check (`derived_environment: pushed_commit_sha`) covers colored services only
under blue-green, and a template should not put `${APP_SOURCE_REVISION}` on a
database: that changes its config every revision and restarts it in step 6,
which fails requests on the side while it serves.

## Eligibility and the fallback

`deploy.strategy` is `auto` (default), `blue-green`, or `in-place`.

- `in-place`: today's apply, unchanged. Use it when the new revision cannot run
  beside the old one against the same data (a destructive schema migration that
  the new code needs before it can pass its health check).
- `auto`: blue-green when every condition below holds, otherwise in-place with
  one progress line, `Blue-green skipped (<reason>); recreating in place`.
- `blue-green`: the same conditions, but an unmet one refuses the apply before
  any effect instead of falling back.

Conditions: an earlier apply recorded this loopback port; the base primary
service is running; no init services are declared; the installed route names `127.0.0.1:P`;
the target is not private (`share.private`, whose apply closes the route
first); S is free in the host port observation; no colored service publishes
host ports, sets `container_name` or `network_mode`, or joins a declared network
other than `default`. A first apply always runs in place: there is nothing to
keep serving.

## Limits

- Share-gate hostnames (`share-<slug>`) are served by the shared gate, which
  forwards to the base app over the base network, so they see the base recreate
  in step 6 as today.
- Shared volumes mean the old and new revision briefly run against the same data.
  Schema changes must be backward compatible for one revision, or the template
  uses `in-place`.
- A real config change to a shared service (a database image bump) still
  restarts it in step 6.
- The apply takes longer: the colored services start twice.

## Template: xspeed-dev-setup full stack

`stacks/hub/overlay/sandbox.full.hosting.yml` sets `deploy.blue_green.shared:
[db, wp-db, wp-setup]`, and `APP_SOURCE_REVISION` is dropped from `db` and
`wp-db`. `edge`, `hub`, `renderer` and `wordpress` are colored (WordPress mounts
`wp_html` but is stateless, and the edge proxies it); `db`, `wp-db`, `wp-setup`,
`mailpit` (publishes its inbox port) and `sandbox-login` are shared.

## Alternatives rejected

- **Two permanent colors** (`-blue`/`-green` projects alternating): every
  consumer keyed on the Compose project (observation, autologin, exec, logs,
  panel, destroy, reaper, resources) would need the active color. Side-loading
  keeps the steady state, so none of them change.
- **Both colors inside one project** (renamed service copies): consumers keyed
  on service names (autologin `service: wordpress`, revision checks, exec) would
  need a color map, and Compose `--remove-orphans`/plain `up` would start both.
- **`--scale` rolling update**: both revisions share one network alias and
  receive mixed traffic; published ports cannot be shared.
