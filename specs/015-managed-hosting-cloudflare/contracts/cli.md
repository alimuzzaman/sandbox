# CLI and Manifest Contract

```text
./sb connect cloudflare [--non-interactive]
./sb remote set-origin NAME --ipv4 ADDRESS [--ipv6 ADDRESS] [--json]
./sb host validate --project-dir DIR [--environment NAME] [--json]
./sb host plan --project-dir DIR --environment NAME --remote NAME [--json]
./sb host apply --project-dir DIR --environment NAME --remote NAME --confirm
                [--allow-zone-ssl-change] [--json]
./sb host secrets --project-dir DIR --environment NAME [--generate] [--set SECRET_KEY]
./sb secrets migrate-zshrc
```

`host apply` never runs without `--confirm`. `host plan` may run without credentials
and reports desired state; with a configured token it reports Cloudflare drift.

Manifest fields: `compose.files`, `compose.service`, `compose.container_port`, optional
`compose.init_services` and `compose.background_services`, `healthcheck.path`, `deploy.allowed_branches`,
`deploy.require_clean`, optional `deploy.min_free_disk_mb`, optional `deploy.derived_environment`, `host.primary`, `host.aliases`, optional
`basic_auth.username`, `basic_auth.password_secret`, and `cloudflare` policy.
Optional `secrets.values`, `secrets.required`, and `secrets.generated` map container
environment keys to public values or names in `~/.zshrc.secrets`; secret values never
appear in the manifest.

`deploy.derived_environment` maps a container environment key to the sole supported
provider `pushed_commit_sha`. It requires `deploy.require_clean: true`, cannot collide
with any declared public/required/generated environment key, and resolves only during
apply from the exact full lowercase 40-hex SHA returned by the source push. Plan output
contains unresolved key/provider metadata; successful apply output contains that
metadata plus the selected SHA, not the rendered environment.

Each redirect alias target is an HTTPS hostname with no path, query, or fragment. Its
hostname is canonicalized to ASCII IDNA before rendering, DNS planning, or persistence;
the original request URI is then appended by Caddy. Redirect aliases may not form a
cycle.

`basic_auth.password_secret` is an owner-only secret-store key, not a Compose
environment key. During confirmed apply, Sandbox streams that secret to remote Caddy's
`hash-password` command and writes only the resulting hash to the managed Caddy
fragment. Plan and validation output report the username and secret key name only.

`cloudflare` accepts exactly one TLS policy:

- `proxied: true`, `tls: origin-ca`, `ssl_mode: strict` keeps Cloudflare at the edge.
- `proxied: false`, `tls: acme` creates DNS-only records and lets Caddy manage a public
  ACME certificate. This opt-in mode exposes the origin and cannot use
  `basic_auth.bypass_ips`, which depends on trusted Cloudflare proxy headers. It accepts
  exact hostnames only and rejects wildcards or existing CNAMEs because public ACME is
  intentionally implemented without remote DNS credentials.
