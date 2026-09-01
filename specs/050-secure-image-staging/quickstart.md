# Quickstart: Secure Private Image Staging

Local implementation validation uses only fake remotes/daemons and synthetic credential
canaries. It does not authorize a live secret, GHCR pull, host mutation, deployment, or production.

## RED-first acceptance

Before production source, add failing tests for plan/policy authority, fixed broker/helper,
all credential surfaces/cleanup paths, descendant ownership, exact pull, coherent local proof,
ledger replay/conflict/uncertainty, proof mutation, and zero activation reachability.

The process gate requires a transient systemd service on cgroup v2 and proves whole-unit
termination from unit state plus cgroup empty/removal. The proof gate covers unchanged
Feature 049 projection/topology, anonymous denial, authenticated pull, full-proof retention,
pinning, compaction, and `proof_expired` non-authority.
It also covers exact cross-store lock order, prepared proof lease/pin before validation,
holder/deadline behavior without auto-unpin, host-acceptance crash recovery, idempotent
promote/cancel/release ownership, compaction exclusion, 4096-tombstone saturation, bounded
ledger bytes, the strict tombstone-full new-unique-request refusal predicate, durable-holder deadline replay,
and `retention_full` before effects.

## Focused checks

```sh
python3 -m unittest \
  tests.test_hosting_image_staging_policy \
  tests.test_hosting_image_staging_repository \
  tests.test_hosting_image_staging_service \
  tests.test_hosting_image_staging_secrets \
  tests.test_hosting_image_staging_process \
  tests.test_remote_hosting_images
python3 -m compileall -q sandbox/hosting/images sandbox/transports/remote_hosting_images.py
git diff --check
```

All subprocess fixtures use `tests.subprocess_support.synthetic_environment`; no test
copies/enumerates the parent environment.

## Later authorized acceptance

Use a disposable non-production host, synthetic read-only test package, exact installed
revision/helper measurement, and no production domain/data. Prove cleanup and process trees
before any Feature 051 activation work.
