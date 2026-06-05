# Sandbox install site

Serves the public one-line installer so end users can set up the sandbox without
the (private) repo:

```bash
curl -fsSL https://sandbox.xc1.app/install.sh | sh
```

It's a tiny **nginx** container serving two files from `./public/`:

- `install.sh` — the bootstrapper (`scripts/web-install.sh` with `BASE_URL` baked in)
- `sandbox-latest.tar.gz` — the packaged runtime (`scripts/make-release.sh` output)

TLS terminates at **Cloudflare** (the `.xc1.app` edge); this origin is HTTP-only
on `:8088`.

## Deploy (on the server, e.g. 45.63.18.41)

```bash
# 1. get this dir onto the server (git pull or copy deploy/install-site/)
cd deploy/install-site

# 2. stage the public files (builds the tarball + bakes the URL into install.sh)
SANDBOX_BASE_URL=https://sandbox.xc1.app ./publish.sh

# 3. serve it
docker compose up -d            # nginx on :8088

# 4. Cloudflare: add DNS A `sandbox` → 45.63.18.41 (proxied, orange cloud),
#    and make the edge forward sandbox.xc1.app → this box :8088
#    (either a Cloudflare origin rule, or the host nginx proxy_pass to :8088).
```

Verify:

```bash
curl -fsSL https://sandbox.xc1.app/install.sh | head      # the script
curl -fsI  https://sandbox.xc1.app/sandbox-latest.tar.gz  # 200, application/gzip
```

## Publish a new release

```bash
SANDBOX_BASE_URL=https://sandbox.xc1.app ./publish.sh
```

Rebuilds `public/sandbox-latest.tar.gz` + `public/install.sh`. nginx serves the
new files immediately — no restart needed (`install.sh` is sent `no-cache`; the
tarball caches 5 min at the edge).

## Files

| File | What |
|---|---|
| `docker-compose.yml` | the nginx service (HTTP :8088, behind Cloudflare) |
| `nginx.conf`         | serves `./public`, correct content-types for `.sh`/`.tar.gz` |
| `publish.sh`         | builds the tarball + stages `install.sh` with `BASE_URL` |
| `public/`            | generated, gitignored — the served files |
