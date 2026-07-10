# Research: Managed Hosting with Cloudflare DNS and TLS

## Decisions

- **Use Cloudflare Origin CA with proxied records and Full (strict).** Origin CA supports
  wildcard SANs and works with strict origin validation; keys stay on the VPS.
- **Use Caddy for all application and redirect routing.** Sandbox already provisions
  Caddy and writes isolated fragment files for remote routes.
- **Use a generic Compose manifest.** Static, WordPress, and Next.js projects all map to
  a web service, a container port, optional init services, and host routes.
- **Normalize IDNs with Python IDNA encoding.** Cloudflare DNS APIs require Punycode and
  the same normalized host must be used for certificates and Caddy.
- **No automatic DNS pruning.** Apply only manages exact declared records; this protects
  email and unrelated services in shared zones.

## Alternatives rejected

- Caddy public ACME certificates: insufficient for the selected proxied Origin CA policy
  and adds wildcard DNS challenge configuration.
- Cloudflare Tunnel: would replace rather than extend the existing VPS/Caddy model.
- Reusing the WordPress instance registry: excludes static and Next.js services and would
  blur the existing registry's ownership model.
