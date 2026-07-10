# Lenzora Dockerization Prompt

Work in `/Users/alim/Sites/git/lenzora`. Read the repository instructions before
editing. Complete permanent Docker deployment for the full Next.js application. Inspect
the current `Dockerfile`, `docker-compose.yml`, `docker-compose.dev.yml`, and
`sandbox.hosting.yml`; an incomplete attempt already exists, so verify every assumption
instead of preserving it by default.

## Required result

1. Production (`lenzora.app`): build/run the full Node 22/pnpm app, PostgreSQL, Prisma
   migrations, persistent file storage, and required Playwright/Chromium runtime
   dependencies. Use a reproducible multi-stage build and lockfile. Expose internal port
   3000 only; do not set host ports or fixed Compose/container/network/volume names.
2. Development (`lenzora.dev`): provide a hot-reload override on internal port 3333,
   isolated from production by Compose project, DB, storage, node_modules, and Next cache.
   It deploys only a clean `dev` branch; production deploys only a clean `main` branch.
3. Use `${SANDBOX_HOST_ENV_FILE:-.env}` for Compose environment input. Sandbox supplies
   a remote `0600` env file; normal local development may still use `.env`. Ensure
   `DATABASE_URL` addresses the Compose PostgreSQL service, never localhost.
4. Configure `lenzora-web`, `lenzora-db`, and one-shot `lenzora-migrate` services with
   meaningful health checks. The migration must complete before the web service is
   accepted as healthy.
5. Keep both public sites behind real WorkOS authentication. Support separate
   production/development PostgreSQL, NEXTAUTH, WorkOS, URL, cookie, admin-allowlist,
   and storage values. Include public build arguments where Next.js compiles public URL
   values into its build output.
6. Update `sandbox.hosting.yml` with both environments, their branch policies, health
   checks, migration service, Cloudflare Origin CA/strict policy, and secret mappings.
   Do not put secret values in the repository.
7. Do not copy `.env`, secret files, Git data, caches, test output, or local storage into
   images.

## Required validation

- Render production and development Compose configuration with a temporary fixture env.
- Build the production image, start services, run migrations, and pass HTTP health.
- Start the development override and verify hot reload starts.
- Run the targeted type check, production build, Prisma validation/migration check, and
  relevant smoke tests.
- Run offline Sandbox host validation for both environments.
- Do not deploy, alter DNS, use real secrets, commit, or push without explicit approval.

Report files changed, commands/tests run, and only the names of missing WorkOS settings.
