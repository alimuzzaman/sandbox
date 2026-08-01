# Contract: Ingress Adapter

Every adapter is registered by `sandbox.ingress.manifest` and receives injected bounded
command, filesystem, secret-reference, and health mechanisms. It does not read project or
route repository JSON directly.

## Required methods

- `detect(listener_snapshot) -> IngressObservation | None` — read-only, evidence-backed.
- `inspect_routes(observation) -> canonical routes` — read-only and bounded.
- `plan_route(selection, naming, backend, prior) -> adapter plan` — pure.
- `validate_current(plan) -> result` — validates the incumbent's complete current state.
- `stage_candidate(plan) -> stage token` — creates only an attributable candidate or
  optimistic API request; no reload/traffic activation.
- `validate_candidate(stage token) -> result` — complete candidate state.
- `activate(stage token) -> result` — atomic activation and documented graceful reload.
- `observe_route(plan) -> canonical route` — exact ownership/target/capability evidence.
- `rollback(stage token, prior) -> result` — restore exact prior owned state and reload.
- `cleanup(route record) -> result` — unchanged state only, repeat-safe.

## Transaction invariant

The shared runner executes:

1. re-observe precondition and foreign collision;
2. validate complete current configuration;
3. capture exact prior owned state and baseline health;
4. stage and validate complete candidate configuration;
5. activate and gracefully reload;
6. observe exact route and perform end-to-end plus baseline health;
7. persist last-applied state only on success;
8. on any failure, restore prior state, reload, and re-run baseline health;
9. report incomplete rollback if restoration cannot be proven.

Configuration-file adapters must use deterministic owned filenames and an ownership header
containing a non-secret route ID. API adapters must use optimistic concurrency and persist
the canonical resource identity returned by the incumbent. A marker without matching
canonical route state never authorizes update/removal.

## Product boundaries

- **Sandbox Caddy**: existing generated configuration, exact dedicated-loopback bind;
  recognizes its own container/process identity.
- **Herd/Valet**: documented CLI route/TLS lifecycle; no private config writes.
- **nginx/Apache/Caddy**: an already-enabled owned-fragment/include surface plus complete
  validation and graceful reload; never edit the primary file.
- **Traefik**: existing enabled file-provider directory only; never change static config.
- **Detect-only products**: `detect`/status/support only; every mutation method is absent
  and capability checks return before side effects.

