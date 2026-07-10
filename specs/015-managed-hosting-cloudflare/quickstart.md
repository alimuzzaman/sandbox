# Quickstart Validation

1. Run `./sb host validate --project-dir /path/to/project` for each project manifest.
2. Run unit coverage with `python -m unittest tests.test_hosting`.
3. Validate Lenzora production and development Compose files with `docker compose config`.
4. Boot the WordPress project through Sandbox and verify its network with `wp site list`.
5. Do not run `host apply` until a Cloudflare token, remote origin address, backup plan,
   and explicit production approval are available.
