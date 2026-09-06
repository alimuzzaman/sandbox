# Edge-cache and production controller reconciliation

Source-only integration of `f0370472dd839c6658eaaf7ea1a0feee00b745fc`
(edge-cache purge) with `7902e44614c152f8f9622ddae55642829edc2eb8`
(authenticated machine identity and production activation fixes).
The merge was conflict-free. Both histories and their matching documentation
and tests are preserved. No controller was installed and no hosted target changed.

Local durable job `0c9410715ec79740ba9c398dfeab205b` completed with
lifecycle `succeeded`, exit 0: 152 tests across edge cache, activation CLI,
private source, provisioning, staging process/secrets/v2, activation v2,
activation recovery, and remote hosting images. `git diff --cached --check`
also passed. This is source validation, not live activation evidence.

The production task still owns the live controller and has a further admission
fix underway. Lenzora must not pin this intermediate merge or advance its held
dev/main branches until that final revision is reconciled and validated.
