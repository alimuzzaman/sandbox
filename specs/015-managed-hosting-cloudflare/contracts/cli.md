# CLI and Manifest Contract

```text
./sb connect cloudflare [--non-interactive]
./sb remote set-origin NAME --ipv4 ADDRESS [--ipv6 ADDRESS] [--json]
./sb host validate --project-dir DIR [--environment NAME] [--json]
./sb host plan --project-dir DIR --environment NAME --remote NAME [--json]
./sb host apply --project-dir DIR --environment NAME --remote NAME --confirm
                [--allow-zone-ssl-change] [--json]
```

`host apply` never runs without `--confirm`. `host plan` may run without credentials
and reports desired state; with a configured token it reports Cloudflare drift.

Manifest fields: `compose.files`, `compose.service`, `compose.container_port`, optional
`compose.init_services`, `healthcheck.path`, `deploy.allowed_branches`,
`deploy.require_clean`, `host.primary`, `host.aliases`, and `cloudflare` policy.
