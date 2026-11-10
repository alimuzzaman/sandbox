# Research: Tailnet-Only Hostnames

All unknowns from the Technical Context are resolved below. Real hostnames,
addresses and zones from the four manual runs are not reproduced; examples use
`example.test`, `203.0.113.x` (public) and `100.64.0.x` (tailnet).

## R1. Certificate client: `lego` or an in-process ACME client

- **Decision**: Run the external `lego` binary (v5 `lego run` syntax) on the
  controller as a child of the secrets broker, with `--dns cloudflare` and
  `--csr <file>`, where the signing request was made on the remote.
- **Rationale**: ACME needs JWS signatures (ES256 or RS256). The controller's
  dependencies are stdlib plus PyYAML/keyring (`requirements-cli.txt`); the
  stdlib has no ECDSA or RSA signing, so an in-process client would mean a new
  cryptography dependency or shelling out to `openssl` for every signature, and
  a DNS-01 solver with propagation checks. `lego` is one static binary, ran
  successfully in all four manual runs, reads the token from
  `CLOUDFLARE_DNS_API_TOKEN` (an environment variable, so the broker can inject
  it) and supports an external CSR.
- **CSR mode**: the remote generates an EC P-256 key with `openssl` under
  `umask 077` and emits only a CSR, matching how the nginx front door already
  makes Origin CA keys (`front_door/nginx.py`, `ensure_origin_certificate`). The
  private key never leaves the remote (FR-010). In CSR mode `lego` writes
  `certificates/<name>.crt` (leaf plus issuer bundle), `<name>.issuer.crt` and
  `<name>.json`, and no key.
- **Re-issue**: `lego run` may decline to replace a certificate that is not
  due. Sandbox decides due-ness itself (30 days) and deletes its own copy of
  `certificates/<name>.*` under the lego data directory before each issue, so
  every `lego run` obtains a fresh certificate.
- **Alternatives**: in-process ACME with a new `cryptography` dependency
  (rejected: new dependency in the shipped CLI, a large surface to own);
  `certbot --dns-cloudflare` on the remote (rejected: the token would have to
  reach the remote and be written to a credentials file there); Tailscale's
  own `tailscale cert` (rejected: issues only `*.ts.net` names).

## R2. Token path: broker child processes only

- **Decision**: Both token consumers run through
  `SecretService.run(source, key, argv, destination="CLOUDFLARE_DNS_API_TOKEN",
  surface="cli")` (`sandbox/secrets/service.py`). The children are fixed: the
  `lego` binary, and a self-contained stdlib program shipped in the package
  (`sandbox/hosting/tailnet_names/cloudflare_child.py`) run as
  `sys.executable -I <path> <json-request>`.
- **Rationale**: The broker builds a minimal environment (`PATH`, `HOME`,
  `TMPDIR`, `LANG`, `TERM`, `LC_*` plus the secret), closes stdin, merges and
  caps output at 1 MiB, redacts every exact secret value from it, kills the
  process group on exit, and audits names only. The existing
  `sandbox/core/_cloudflare.Client` resolves `CLOUDFLARE_API_TOKEN` into the
  Sandbox process itself, so it cannot be reused without breaking FR-005.
- **Limit**: `run_many` refuses provider-backed sources (Infisical); the token
  must be in a file source such as `personal`. A provider source would need a
  registered use profile, which is out of scope.
- **Child request**: the non-secret request (operation, zone, name, address,
  record id) is one JSON argv element; the child prints one JSON line as its
  last output line. The API base URL is a constant in the child, never read
  from the environment, so a request cannot redirect the token.

## R3. Configuration

- **Decision**: A `tailnet_names` block in `$SANDBOX_HOME/sandbox.local.yml`:

  ```yaml
  tailnet_names:
    zones: [example.test]            # required allow-list
    cloudflare_token:                # broker reference, never a value
      source: personal
      key: EXAMPLE_CF_API_TOKEN
    acme_email: ops@example.test     # required
    acme_server: letsencrypt         # optional: letsencrypt | letsencrypt-staging | https URL
    lego: /opt/homebrew/bin/lego     # optional; default: `lego` on PATH
  ```

  The zone for a name is the longest configured zone that is a proper suffix
  of it; its Cloudflare id is looked up by exact name at run time.
- **Rationale**: `sandbox.local.yml` is the documented place for per-machine
  overrides, and the block holds only names. `--acme-server` may override the
  directory on one call so an operator can try the staging authority.
- **Alternatives**: per-remote registration fields (rejected: the zone and the
  token belong to the operator's DNS account, not the server); CLI flags for
  the token reference (rejected: easy to get wrong per call, and the brief asks
  for no per-call secret plumbing).

## R4. Remote access

- **Decision**: Use the registered remote's transport, `_remote.ssh_run`, the
  way `sb host apply` and the nginx front door do (the front door's `_ssh` and
  `_python_program` wrappers). Remote work is either the front door's existing
  route transaction (`nginx.apply_route` / `nginx.remove_route`) or one
  self-contained remote program run as `sudo -n python3 -c` with a JSON payload
  on stdin (the `_python_program` pattern).
- **Rationale**: The brief says "go through the control service, the way other
  host operations do". All host and edge operations reach the remote through
  this registered transport, not an operator's ad-hoc `ssh`. No new transport
  is added.

## R5. nginx block, file ownership and the transaction

- **Decision**: Route name `tailnet-<fqdn>`, so the file is
  `/etc/nginx/conf.d/sandbox-tailnet-<fqdn>.conf` (root, 0600) via
  `nginx.route_file`. Install, `nginx -t`, reload and restore use
  `nginx.apply_route(entry, name, content)` with no marker probe (the block is
  not on loopback). That transaction already runs under
  `flock /run/lock/sandbox-edge-nginx.lock`, restores the previous file (or
  deletes a new one) when `nginx -t` fails, and does not reload. An unchanged
  digest is a no-op without a reload, which gives idempotency for free.
- **Certificates**: `/etc/nginx/sandbox-tailnet/<fqdn>/` (root, 0755) with
  `privkey.pem` (root, 0600), `fullchain.pem` (root, 0644) and `request.csr`
  (0644). nginx's master process reads them as root.
- **Renewal**: changing only `fullchain.pem` does not change the route digest,
  so renewal has its own transaction in the remote program: take the edge lock,
  keep the previous chain, install the new chain, run `nginx -t`, reload; on
  failure restore the previous chain and reload only if the test passes again.
- **Block contents**: a header comment carrying
  `# sandbox-tailnet-name {"fqdn":...,"upstream":...,"tailnet_ip":...}` (no
  secrets), so `list` reads the truth from the remote; port 80 returns
  `308 https://$host$request_uri`; port 443 `ssl` plus `http2 on;` (nginx
  ≥ 1.25.1, else `listen ... ssl http2`), using `nginx.features()` from the
  preflight facts; `proxy_pass http://<upstream>`, HTTP/1.1, `Host $host`,
  `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto https`, `Upgrade` and
  `Connection "upgrade"`, `proxy_buffering off`, `proxy_read_timeout 600s`,
  `client_max_body_size 50m`. No `default_server` anywhere.
- **Preflight**: `nginx.preflight(entry, declared=[fqdn], own_file=path)`
  refuses when nginx is missing, inactive, already failing `nginx -t`, missing
  the `conf.d` include, or when another file serves the name.

## R6. Gate

- **Decision**: Before the first remote change and again right before the
  server-block or certificate transaction: (a) the remote program probes
  `sandbox.resources.host_guard.APPLY_TRANSACTION_LOCKS` (the hosting Caddy,
  edge nginx and Docker pool locks) with `LOCK_SH | LOCK_NB` and reports any
  held lock; (b) the controller asks the remote's durable-job controller for
  active jobs through `RemoteJobTransport.list(remote, active_only=True,
  limit=1)`, a control-only call that needs no readiness gate. Either one busy
  means refusing with `tailnet_name_busy`; an unanswerable job query means
  refusing with `tailnet_name_gate_unavailable` (fail closed). Never wait.
- **Controller side**: one non-blocking `flock` per remote on
  `$SANDBOX_HOME/runtime/tailnet-names/<remote>.lock` serialises this
  controller's own runs.

## R7. Verification

- **Decision**: In-process on the controller with the stdlib `ssl` module:
  connect to `<tailnet-ip>:443` with SNI `<fqdn>` and verification off, take
  the peer leaf (DER), and compare its SHA-256 with the fingerprint the remote
  reported for the installed `fullchain.pem` leaf. Then send
  `GET / HTTP/1.1` with `Host: <fqdn>` and read one status line. Pass when the
  fingerprint matches and a status line arrives that is not 502 or 504.
- **Public check**: connect to the public address on 443 with the same SNI. It
  passes when the TCP connect fails, the TLS handshake fails, or the handshake
  completes with a leaf other than ours. It fails as an exposure only when our
  leaf is presented; then the block is removed and nginx reloaded.
- **Rationale**: Comparing fingerprints proves "our certificate on our
  socket" without depending on the controller's CA bundle (a python.org build
  on macOS may have none) and works with a staging authority. Public trust of
  the issued chain is the authority's job.
- **Public address**: `remote_ssh_parts(entry)["host"]`, resolved to IPv4 and
  accepted only when `ipaddress.ip_address(...).is_global`; `--public-address`
  overrides it.

## R8. Upstream validation

- **Decision**: `HOST:PORT` where HOST is `localhost` (rendered as
  `127.0.0.1`) or an IPv4 literal that is loopback, RFC 1918 private, or in
  `100.64.0.0/10`; port 1-65535. Hostnames are refused, because nginx resolves
  them once at start-up and a failure there breaks every site.

## R9. Controller state

- **Decision**: `$SANDBOX_HOME/runtime/tailnet-names/<remote>.json`
  (0600, written with tempfile plus `os.replace`), keyed by name, holding the
  fields in data-model.md. It records the Cloudflare record id after the DNS
  step (also for an adopted identical record), which `remove` requires before
  it deletes a record.
- **Truth**: the remote is authoritative for the block and the certificate;
  Cloudflare for the record. The state file is the controller's memory of what
  it did, used for record ownership and drift reporting.

## R10. What this feature does not change

- `sandbox/commands/ui_dash.py`, `sandbox/hosting/panel_*`, `src/web` (the
  panel thread adds `sb web --allow-host`); `sb host stack` and reap.
- The existing Cloudflare client and its callers.
- Manually published names: a name already served by a hand-written block
  (not `sandbox-tailnet-*.conf`) is refused by the preflight conflict check.
  Migrating one means removing the manual block first, which is the operator's
  call.
