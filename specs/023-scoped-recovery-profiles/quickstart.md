# Quickstart Validation: Scoped Recovery Profiles

All server interactions use `./sb recovery ...` or the corresponding MCP tools.

1. Run focused model/catalog/service tests. Expected: invalid roots, cycles, shell strings,
   secret fields, and unknown adapters fail before side effects.
2. Run `./sb recovery profiles --remote scaleway-sandbox --json`. Expected: four initial profiles
   and no secret values.
3. Run `./sb recovery plan --remote scaleway-sandbox --json`. Expected: disposable WordPress
   state and containers excluded; valuable production/control-plane artifacts explicitly planned.
4. Capture fixture profiles with a sentinel passphrase environment. Expected: complete fixture
   manifest, verified hashes, no sentinel in output/process arguments/manifest.
5. Inject capture, encryption, upload, check, and manifest failures. Expected: no complete set and
   prior sets unchanged.
6. Run restore plan against fixture set. Expected: zero writes and ordered checkpoints/actions.
7. Apply fixture restore into an allowed disposable root and inject a verification failure.
   Expected: successful normal restore; failed case rolls back to the checkpoint.
8. On the remote, create one real set only after read-only profile discovery is reviewed. Verify
   download/decrypt/integrity before considering schedule activation or legacy pruning.
9. Run schedule and retention plans. Expected: disabled timer plan, overlap/resource gates, and
   protected newest/only verified set.
10. Run a fresh-server disposable drill from clean checkout + approved secrets + set ID. Existing
    WordPress/Hermes/public-dashboard acceptance checks must remain stable.

Protected final actions—real restore, schedule activation, and legacy deletion—are separate and
must not be inferred from successful validation.
