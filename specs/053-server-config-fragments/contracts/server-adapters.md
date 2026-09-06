# Server Adapter Contract

## Adapter Manifest

Adapters register through one deterministic manifest. V1 entries are exactly:

| Public `server_type` | Adapter ID | Minimum support |
|---|---|---|
| `nginx` | `wordpress-cache/nginx/1` | apply/list/show/revert/validate/activate/rollback |
| `litespeed` | `wordpress-cache/openlitespeed/1` | apply/list/show/revert/validate/activate/rollback |

`apache` and `herd` return `server_unsupported`; they are not aliases or fallbacks.
Duplicate IDs/types fail composition. An adapter declares authority versions, renderer
revision, accepted active image family constraints, web service names, mount layout, and
readiness contract.

## Adapter Protocol

An adapter implements typed methods. It never receives an unrestricted command runner,
registry mapping, whole environment, or caller path.

```text
policy(fragment metadata + exact bytes, instance facts) -> PolicyEvidence
render(ordered accepted fragments, instance facts) -> RenderedGeneration
observe_runtime(instance handle, deadline) -> RuntimeObservation
validate(rendered generation, exact runtime observation, deadline) -> ValidationEvidence
activate(generation ID, exact runtime precondition, deadline) -> ActivationEvidence
reload(exact runtime precondition, deadline) -> ReloadEvidence
observe_ready(expected generation, exact runtime precondition, deadline) -> ReadinessEvidence
restore(prior generation, exact runtime precondition, deadline) -> ActivationEvidence
```

The injected runtime gateway exposes fixed argv operations only: selected-instance
Compose inspect/create/start/exec/stop/removal needed by the adapter, bounded HTTP
readiness, and monotonic deadline. Adapter code builds every argv element. Fragment bytes
never become argv, shell source, environment, labels, or container names.

Every result is typed and content-free. Native stdout/stderr is bounded internally for
classification, redacted, then discarded.

## Common Runtime Preconditions

Before validation and again immediately before activation, adapters prove:

- authoritative instance incarnation and selected service ownership;
- server type and adapter ID;
- exact running container/runtime identity;
- content-addressed active image ID;
- expected instance-specific read-only mount ID and source root;
- current effective generation ID;
- no unrelated web service is selected;
- phase deadline remains.

Any mismatch invalidates validation. A tag, Compose service name, port, or container name
alone is insufficient.

## nginx Adapter

### Runtime layout

- Keep `config/nginx-sandbox.conf` as the Sandbox base vhost authority.
- Add one fixed include inside that vhost for the selected instance's mounted active
  generation. The include cannot be caller-controlled and is outside Caddy/host ingress.
- Each nginx Compose service mounts only
  `$SANDBOX_HOME/runtime/server-config/<its-incarnation>/` at the fixed adapter guest root,
  read-only. No two instances share a fragment root or writable generation.
- The active generation contains a deterministic combined fragment file plus manifest.
  Base front-controller, PHP upstream, health/autologin, and protected locations remain
  adapter-owned and cannot be replaced by fragments.

### Validation

1. Confirm common and nginx policy for every fragment and combined set.
2. Render the same base-vhost context and fixed include layout used live, with a synthetic
   empty WordPress document root containing only adapter canary/cache fixtures.
3. Create a disposable container from the exact active nginx image ID, network `none`,
   read-only root, no live volumes/environment/secrets, and bounded tmpfs.
4. Run the exact image's native `nginx -t` via fixed argv.
5. Require success plus canonical manifest proof that each fragment marker is included
   exactly once and protected base routes remain adapter-owned.
6. Remove the validation container inside the deadline. Cleanup uncertainty is a failed
   validation, never permission to activate.

### Activation and readiness

- Atomically select the candidate generation under the mounted incarnation root.
- Invoke a fixed target-only config test/reload against the selected nginx Compose service.
- Readiness requires bounded HTTP success on the existing instance route, exact effective
  generation proof, unchanged runtime/image/mount facts, and a live nginx process.
- Identical no-op performs none of these calls.
- Rollback selects the exact prior generation and performs one target-only recovery reload
  and readiness observation.

## OpenLiteSpeed Adapter

### Runtime layout

- Canonical public type is `litespeed`; adapter/reporting text may say OpenLiteSpeed.
- The existing image continues to own server/listener/admin/global configuration.
- Sandbox supplies one complete instance-vhost generation through a fixed per-instance
  read-only mount/inclusion point. The adapter renders baseline WordPress rewrite/PHP
  behavior plus the accepted cache subset; fragments cannot set vhost root, docroot,
  listener mapping, external processors, admin, TLS, or global cache authority.
- The adapter does not append to or overwrite WordPress/plugin `.htaccess`. Existing
  plugin-owned bytes remain separate. If the exact image cannot support the fixed vhost
  boundary, the capability is unavailable for that image.

### Exact-image isolated validation

1. Resolve the selected live `wp` service to its content-addressed image ID and record the
   current runtime/mount precondition digest.
2. Render a disposable vhost generation with the complete fragments, an adapter-owned
   static origin/cache fixture, protected-origin canary, and inclusion markers.
3. Create a disposable container using exactly that image ID with:
   - network mode `none` (loopback only);
   - read-only root filesystem;
   - no live instance data, DB, uploads, plugin source, Docker socket, credentials,
     inherited environment, or mutable config;
   - only adapter generation/fixtures mounted read-only and bounded tmpfs for required
     OLS runtime paths;
   - fixed entrypoint/argv selected by the adapter.
4. Start OLS within the validation deadline. A status acknowledgement alone is not proof.
5. Execute a fixed in-container loopback probe supported by the exact image. Require the
   candidate vhost, every inclusion marker, cache behavior canary, protected origin/PHP
   route, and health route to produce the expected bounded results. Detect ignored or
   unreachable directives as failure.
6. Stop/remove the validator and prove cleanup. Missing probe tooling, ignored directives,
   unprovable inclusion, or cleanup timeout returns a validation refusal before live
   activation.

No validator port is published and no live network namespace/volume is joined.

### Activation and readiness

- Atomically select only the target incarnation's candidate vhost generation.
- Invoke the selected instance's fixed OLS graceful restart/reload path. No server-global
  or other Compose project is addressed.
- Readiness requires bounded HTTP origin success, exact generation evidence from the
  target vhost, unchanged runtime/image/mount facts, and live OLS process proof.
- Cache hit/purge behavior is a live acceptance concern after activation; validation's
  synthetic canary proves the configuration is active, not that a particular plugin has
  warmed its real cache.
- Rollback restores the prior complete vhost generation and performs one recovery restart
  plus readiness proof.

## Runtime Regeneration and Mount Attachment

- New/reconciled Compose renders include a mount derived from the authoritative opaque
  incarnation, never from user fragment/name/path input.
- A running legacy web service without the expected mount is not transparently recreated
  by `server config`. Mutation refuses with the supported `sb apply --instance NAME`
  remedy. After apply, runtime observation must prove the mount before any candidate is
  accepted.
- Compose regeneration cannot inspect fragment JSON or choose a generation. It renders
  the fixed mount only; repository/service reconciliation owns state.
- Relocation rebinds the same incarnation root through existing Sandbox-home migration
  rules and requires generation/mount re-observation before readiness.

## Readiness Contract

`ready` requires all of:

- target web process accepts traffic;
- existing WordPress readiness route returns its expected success class;
- effective generation equals the expected generation;
- incarnation, server, runtime, image, and mount match the operation preconditions;
- observation completes before the deadline.

Stopped, unavailable, timeout, partial, stale, or generation-unobservable results are
`stopped`, `unknown`, or `degraded`, never `ready`.

## Isolation Evidence

For every live target operation, record a control instance before and after:

- incarnation and runtime identity;
- exact image identity;
- fragment-set/effective-generation identity;
- response marker;
- readiness.

All must remain equal. Adapter unit tests also assert every runtime gateway call carries
the target Compose project/service identity and that validator calls use no target/control
network, data, or config mount.

## Failure and Cleanup

- Validation failure: no live pointer, reload, or committed state change.
- Activation/reload/readiness failure: service invokes one journal-bound rollback.
- Validator cleanup failure: validation fails and reports bounded cleanup status; live
  target remains unchanged.
- Rollback failure/unknown: `recovery_needed`; no later candidate mutation.
- Adapter methods never catch a timeout and retry with a new container/identity. The one
  service deadline and transaction identity govern the whole attempt.
