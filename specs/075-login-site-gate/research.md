# Research: login service site gate

Each decision below resolves a design question the spec left to planning.

## R1. Where the link token travels

- **Decision**: In the URL fragment, as the existing entries do
  (`https://<gate><path>#sandbox_autologin=<token>`). The page served at the link path
  reads the fragment, clears it from the address bar, and submits it by POST on its own.
- **Rationale**: A fragment never appears in a request line, so no access log on the
  edge, Cloudflare or the gate records it. Unfurlers fetch the URL without running
  scripts, so they never see or spend the token (FR-006, FR-008).
- **Alternatives**: Token in the query string (logged by every proxy; rejected). A
  confirmation click as in `inbox_gate` (safer against script-running previewers, but
  the owner asked for "opening the link" to land on the site; kept as the no-script
  fallback: the page still shows a Continue button).

## R2. Link and session state

- **Decision**: Files on the login service's existing private tmpfs
  (`/run/sandbox-login`), one directory per entry: `links/<token_sha256>.json`,
  `used/<token_sha256>.<n>` markers, `sessions/<secret_sha256>.json`.
- **Rationale**: The tmpfs already exists, is mode 0700 for the service user, and
  vanishes with the container. Server-side session records are what make list and
  revoke possible; the `inbox_gate` HMAC session has no revoke. Naming files by hash
  means the gate never stores a secret, and a lookup is one `open()`.
- **Alternatives**: HMAC cookies with a revocation list (two mechanisms, still needs
  state); process memory (lost on a process restart, and the operator cannot reach it
  through `compose exec`).

## R3. Multi-use links without races

- **Decision**: A link with `uses: N` is spent by creating marker `used/<hash>.<k>` with
  `O_CREAT|O_EXCL` for the first free `k < N`.
- **Rationale**: The same primitive the existing modes use; exactly one of any number of
  concurrent openers gets each slot (SC-004). No lock file is needed.

## R4. Revoke versus an in-flight consume

- **Decision**: Consume writes the session, then re-checks that the link record still
  exists, and deletes the session if it does not. Revoke deletes the link record first,
  then every session whose `link` is that id.
- **Rationale**: Whichever order the two interleave in, no session from a revoked link
  survives. This needs no lock across processes (the operator's revoke runs in a separate
  `compose exec` process).

## R5. Operator list, mint and revoke

- **Decision**: The controller runs the same `runtime.py` inside the running service:
  `compose exec -T sandbox-login python3 -c <loader> <source> site <verb> <entry> [...]`.
  Mint sends the record (hash only) on stdin. Output is one JSON line of ids, times and
  counts.
- **Rationale**: One implementation of the storage layout; the container's own
  `SANDBOX_LOGIN_CONFIG` decides whether the entry exists and is a `site_gate`, so a
  target applied before this feature refuses with `site_entry_unknown` instead of
  writing files nothing reads.
- **Alternatives**: A separate management program string in `__init__.py` (a second copy
  of the layout to keep in step).

## R6. Showing the link only on the operator's terminal

- **Decision**: The controller opens the controlling terminal (`/dev/tty`) before it
  mints. If there is none, it refuses with `terminal_required` and mints nothing. The
  link is written there once. Stdout, with or without `--json`, carries the link id,
  expiry and uses only.
- **Rationale**: Under `sb job-start`, an agent's shell, MCP, or a pipe there is no
  controlling terminal, so the value cannot land in job output, transcripts or feedback
  (FR-005). Refusing before minting leaves no orphan live link.
- **Alternatives**: Print to stdout unless `--json` (an agent capturing stdout would still
  get it; rejected). A `--link-file` option (more surface; not asked for).

## R7. Rate limiting

- **Decision**: A per-entry sliding window in the gate process: 30 link POSTs per 60
  seconds, then 429 until the window frees.
- **Rationale**: All traffic arrives from the project's edge, so the client address is the
  edge's and a per-address limit would be either global anyway or based on a spoofable
  forwarded header. Tokens are 256-bit, so the limit protects the file system and logs,
  not the secret.

## R8. Open-proxy and Host tricks

- **Decision**: Forward only when the request target is origin-form (`/...`, not `//...`),
  there is exactly one `Host` header, and it equals the gate hostname. Connect only to the
  declared upstream host and port, and send `Host: <gate hostname>` explicitly.
- **Rationale**: The connection target never comes from the request, and duplicate `Host`
  headers cannot make the upstream pick a different virtual host than the gate checked.

## R9. Upstream exposure and Sandbox basic auth

- **Decision**: Apply preflight refuses an upstream that publishes a port beyond loopback
  (`upstream_port_published`) or is missing (`upstream_not_in_project`). Manifest parsing
  refuses `basic_auth` together with a `site_gate` entry.
- **Rationale**: A publicly published upstream makes the gate pointless. Sandbox's
  `basic_auth` covers every served hostname, so it would put a password prompt in front of
  the gate and break the "no account" promise.

## R10. Keeping the site private

- **Decision**: Documented, not enforced: every hosted hostname, the primary included, has
  a public DNS record, so the project's edge must answer the primary with 404 (or route it
  only to the gate as well) and the owner views the site through the upstream's
  loopback-only port, for example a tailnet proxy on the server.
- **Rationale**: The scout found no tailnet or loopback-only mode for hosted primaries
  (`sandbox/core/_hosting.py:688-701`). Adding one is out of scope.

## R11. Migration

- **Decision**: No `sb remote service migrate`. Host commands run from the controller over
  SSH. A target picks up the new runtime on its next `host apply`, which recreates the
  `sandbox-login` container because its rendered command changed.
