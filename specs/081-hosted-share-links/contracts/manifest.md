# Contract: manifest fields

## `share` (per environment)

Short form (no `autologin` block):

```yaml
share:
  hostname: s7k2.dev.example.test      # a served alias; the only public way in
  private: true                        # primary and every other name answer 404
  agent_mint: true                     # agents may mint links and receive them
  image: python:3.13-alpine@sha256:<digest>
  enabled_until: 2026-12-01            # required; within 30 days of apply
  allowed_host_suffixes: [dev.example.test]
  session_ttl_seconds: 43200           # optional; 300 to 172800
  stack_sign_in: [qa]                  # optional; needs autologin.entries
```

With an `autologin.login_service` block, `share` carries only `hostname`, `private`,
`agent_mint` and `stack_sign_in`, and uses the login service's fields.

Rules:

- `hostname` is a served, non-primary, non-wildcard alias, and fronts no other entry.
- `share` is a reserved entry name in `autologin.entries`.
- `basic_auth` with `share` is refused, as with `site_gate`.
- With `private: true`, apply preflight refuses an application service that publishes a
  port beyond loopback (`upstream_port_published`).

## `indexing` (per environment)

`noindex` (default) or `public`. Under `noindex`, both front doors add
`X-Robots-Tag: noindex, nofollow, noarchive` to every response, and `robots` defaults to
`deny`. `robots: allow` requires `indexing: public`.

## `autologin.entries.<name>` of mode `site_gate` (075, amended)

- Links open at `https://<hostname>/s/<id>#<token>`. A declared `path` is accepted for
  compatibility and ignored, with a validation warning.
- `ttl_seconds` stays the default link lifetime for `host login-url` (60 to 3600).
