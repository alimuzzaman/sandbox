# Read-only Recovery Profile Inventory

Collected through `./sb recovery plan --remote scaleway-sandbox --json`.

| Target | Production discovery | Recovery decision |
|---|---|---|
| Control plane | Sandbox home `/home/alim/sandbox`; Hermes and hosting managed by Sandbox | Capture safe Sandbox/Hermes/Cloudflare declarations and approved encrypted credentials; exclude sessions/jobs/logs/caches/downloaded runtimes/dev WP snapshots |
| lenzora | Production PostgreSQL volume at `/var/lib/postgresql/data`; production `/app/storage` volume; separate development DB/storage/cache volumes; clean Git checkout | Capture production DB and production storage only; recover code from Git; exclude all development and cache volumes |
| alimuzzaman-me | Production web container has no persistent mount; Git checkout on `master` is clean | Store Git provenance; do not duplicate source tree; add partial filesystem paths only if future discovery finds non-Git persistent state |
| amarsonar-bangla | Production DB volume, full `/var/www/html` volume, named uploads volume; clean Git checkout | Capture consistent DB and full WordPress directory, including uploads; exclude only reviewed transient cache/log paths |

No environment variables, database credentials, API tokens, file contents, or passphrase values were read or recorded.
