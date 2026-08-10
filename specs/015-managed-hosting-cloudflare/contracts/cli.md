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
`deploy.require_clean`, `host.primary`, `host.aliases`, optional
`basic_auth.username`, `basic_auth.password_secret`, and `cloudflare` policy.
Optional `secrets.values`, `secrets.required`, and `secrets.generated` map container
environment keys to public values or names in `~/.zshrc.secrets`; secret values never
appear in the manifest.

Each redirect alias target is an HTTPS hostname with no path, query, or fragment. Its
hostname is canonicalized to ASCII IDNA before rendering, DNS planning, or persistence;
the original request URI is then appended by Caddy. Redirect aliases may not form a
cycle.

`basic_auth.password_secret` is an owner-only secret-store key, not a Compose
environment key. During confirmed apply, Sandbox streams that secret to remote Caddy's
`hash-password` command and writes only the resulting hash to the managed Caddy
fragment. Plan and validation output report the username and secret key name only.
