# Permanent Docker Hosting Readiness

## Outcome

Prepare permanent Docker deployments through the provisioned Sandbox remote without
creating a preview instance or changing live DNS. The personal static site and Bangla
WordPress network are maintained here; Lenzora is delegated to its own agent.

## Secret model

`~/.zshrc.secrets` is the owner-only, non-Git source of personal API and hosting
secrets. `.zshrc` sources it. Sandbox reads only manifest-declared variables and renders
a remote `0600` Compose environment file. No secret is committed, logged, or returned
by the CLI.

## Deployment model

`host apply --confirm` transfers the approved local checkout to the registered remote,
uses an isolated Compose project, runs init jobs, checks loopback health, generates an
Origin CA key/CSR on the VPS, validates Caddy, upserts declared proxied DNS records, and
verifies the edge. It restores Caddy, DNS, and strict-mode changes when verification
fails. Strict-mode changes still require `--allow-zone-ssl-change`.

## Repository policy

- `alimuzzaman.me`: clean `master`, production only.
- `amarsonar-bangla`: clean `master`, production only; immutable WordPress image,
  persistent DB/uploads, and generated DB/admin secrets.
- `lenzora`: its own agent owns full Docker and hosting-manifest completion; permanent
  production is clean `main`, development is clean `dev`.

## Preconditions for a future live apply

- `CLOUDFLARE_API_TOKEN` has Zone DNS Edit, Zone Settings Edit, and SSL and Certificates
  Edit.
- Required WordPress admin email and Lenzora WorkOS credentials have been set through
  the secret-file workflow.
- A reviewed `host plan` is clean and the user explicitly approves `host apply`.
