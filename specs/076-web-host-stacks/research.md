# Research: Stacks and Hosted Sites View

## R1. What the server already knows

- **Decision**: Write a new secret-free `summary.json` per target on the remote during `sb host apply`.
- **Rationale**: Inventory of what is already on the remote (2026-11-10, read from `sandbox/commands/hosting.py`):
  - `runtime/hosts/<p>/<e>/` holds `compose.override.yml` (loopback port), `environment.env` (real secret values, never to be read), `apply.log`, `recovery-phases.json` and sometimes `.initializer-started-at`.
  - `deploy-src/hosts/<p>/` holds a git checkout whose ref name encodes the commit.
  - No file on the remote records hostnames, request id, structured apply status or anything about a stack.
  - On the nginx front door, route files are `root:root 0600`, unreadable to the Sandbox user. On Caddy they are 0644, but they also hold the basic-auth bcrypt hash, so parsing them would put the panel next to credential material.
  - The stack registry is controller-only (`runtime/stacks/registry.json`). Spec 071 (`spec.md` 280-282) says so explicitly.
- **Alternatives considered**:
  - Parse `apply.log` and the route files: incomplete (no request id, no stack) and unreadable on nginx.
  - Copy the controller stack registry to the remote: it carries controller paths, worktree lists and every stack on every remote, which is more than needed.
  - Serve stacks from the controller only: fails the brief, because the panel on the server must show them.

## R2. Where the summary write hooks in

- **Decision**: Use a `SummaryRecorder` object. `_apply_host` calls `started()` right after the source publish checkpoint, the first point where the remote already has an effect. It calls `finished(True)` before returning. The caller wraps `_apply_host` in `recorder.run(...)`, which writes `failed` on any exception after `started()`. Every write is best-effort: exceptions are swallowed and kept on the recorder.
- **Rationale**: This keeps the `hosting.py` diff to a handful of lines, which matters because `feat/host-exec` edits the same file. Writing only after an effect already exists means a refused apply leaves nothing on the remote.
- **Alternatives considered**: Wrapping the whole `_apply_host` body would re-indent hundreds of lines. Writing before preflight would mutate the remote for applies that are then refused.

## R3. The request id inside the apply

- **Decision**: Take `request_id` from the durable job context `_apply_host` already validates (`validate_durable_context` returns `request_id`).
- **Rationale**: Every stack apply runs as a durable `job-start` job. A direct apply outside a job is already refused by `_durable_host_context`.

## R4. The WordPress association mechanism

- **Decision**: Add `sb deploy --stack <slug>` (requires `--expose`). Before any effect, it checks the slug format and that the controller stack registry has `<remote>/<slug>`. After a successful exposed deploy, it writes `runtime/stack-sites/<slug>/<hostname>.json` on the remote and appends a `sites` entry to the controller stack record. A failure to record does not fail the deploy; the JSON result reports `stack_link.recorded=false`.
- **Fallback**: When no link exists, a server-known instance hostname of the form `<prefix>-<slug>.<zone>` is shown under the stack labelled "matched by name". `<zone>` is the suffix after `-<slug>.` in one of the stack's own route hostnames. On the server, the known instance hostnames are registry `url` hosts plus the names of readable Caddy instance route files (`/etc/caddy/conf.d/sandbox-instance-<host>.caddy`). Only file names are read, never contents.
- **Alternatives considered**: Pattern matching alone is ambiguous, and the owner asked for explicit first. A `stack` field on the instance registry record would change the registry schema, which the constitution's principle II governs, for a display concern.

## R5. Health checks

- **Decision**: An in-process `HealthCache` with a 60 s TTL and a 4 s timeout. A single refresher thread runs at most 4 probes at once, capped at 64 hostnames per round. A probe is `GET <healthcheck path or />` over HTTPS with certificate verification, using `http.client`, so redirects are never followed. It sends no cookies or auth. Classification: 2xx is `up`; 3xx is `redirect`; 401/403 is `up_login`; other 4xx is `client_error`; 5xx is `server_error`; any socket, TLS or timeout error is `unreachable`. Only names matching a strict hostname pattern (no wildcard, no port, no userinfo) from Sandbox records are probed.
- **Rationale**: Meets "one bounded probe per hostname, cached, never block". There is one cache per `sb web` process, so many viewers share it (SC-005).
- **Alternatives considered**: Probing in the browser is blocked by CORS and would leak the viewer's network. Probing on each request would block the page.

## R6. The view on a controller

- **Decision**: Build the same view from `stack_registry.read()` and `hosting.load_host_state()`, with last apply from `host_stack._last_apply_view`, which reads the controller job registry. Plain targets are the `hosts.json` entries whose `remote/project/env` no stack record claims. When both sources name the same `remote/project/env`, the server source wins.
- **Rationale**: Reuses the readers `sb host stack list --json` already uses.

## R7. Template path

- **Decision**: Record the template path relative to the stack's checkout (`StackRecord.checkout`), falling back to the file name.
- **Rationale**: The absolute path is a controller-local detail (a user's home directory). The relative path is what a reader needs to find the template in the repository.

## R8. Reading lifetime records on the server (follow-up)

- Decision: add `Store.peek(now)` to `hoststore.py`. It is lock-free and writes nothing. It skips symlinks and non-regular files, reads at most 256 KiB per file and 500 files, and returns `view()` dicts and `invalid` entries.
- Rationale: gotcha 35 keeps every read, validation and eligibility rule in `hoststore.py`. `Store.load()` is unbounded and follows symlinks. `list` takes the exclusive lock and creates `.lock`. A panel must not do either.
- Alternatives: call `list` through the store program (it takes a lock and writes `.lock`); read files in `panel_records` (that duplicates the store's reading rules).

## R9. Lifetimes on a controller

- Decision: run `sb host stack list --remote R --json` as a subprocess from a background cache, one remote per job, and parse each row with `hoststore.parse_record`, then `view(record, now)`.
- Rationale: the brief names this command as the controller contract. A subprocess keeps the panel off `host_stack.py`, which another thread owns. Re-parsing applies the store's own rules to data that crossed a process boundary.
- Alternatives: import `host_stack._list` (private, owned elsewhere); SSH from the panel (duplicates the client).

## R10. Apply outcome without touching `hosting.py`

- Decision: `SummaryRecorder.run(apply)` defers the `finished(ok)` call made inside the apply, then finishes from `result["delivery"]` (`execution_state`, `delivery_state`, `reason`) once the apply returns.
- Rationale: the bool at the call site loses the difference between "execution failed" and "delivery unverified". The delivery summary is already in the returned result. `hosting.py` is on a path another thread owns.
- Alternatives: change the call site to pass the delivery dict. That is the cleaner one-line follow-up once the other thread lands.

## R11. Host pages on a controller

- Decision: host inventory rows carry the remote's own `lifetime` view, so a controller's host page shows lifetimes from the remote's inventory with no extra SSH.
- Rationale: the inventory is built on the host that holds the records, and its shape is not protocol-fingerprinted (`shapes.py` does not list it).
- Limit: the change reaches a remote only after its runtime is updated.

## R12. TypeScript tests

- Decision: put the pure labelling and ordering decisions in an import-free module, `src/web/src/ui/hostlabels.ts`, tested with `node --test` the way `ui/poller.ts` is.
- Rationale: render modules import the DOM and cannot load in plain node.
