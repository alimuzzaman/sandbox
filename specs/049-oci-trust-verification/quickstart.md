# Quickstart: OCI Trust and Verification

This guide proves only pure local source behavior. It does not access a registry,
credential, Docker daemon, remote host, deployment, or production service.

## RED-first contract checks

The feature owner explicitly changed implementation order for this repair: production
source and documentation were written before the real focused tests. No RED selector
was executed, so there is no observed RED evidence. The intended selector is:

```sh
python3 -m unittest \
  tests.test_hosting_image_trust \
  tests.test_hosting_image_contracts \
  tests.test_hosting_image_boundaries
```

Expected pre-source failures were missing `sandbox.hosting.images`, missing config
providers, no verified-plan consumer, and no stable pure refusal contract. These are
expectations only, not executed evidence.

The normal pre-implementation RED checklist would have covered:

1. canonical valid plan generation and input-order invariance;
2. every authority substitution and policy/receipt/provenance mismatch;
3. tag/index/platform/config/topology refusal;
4. closed-schema and size bounds;
5. plan-field mutation refusal by consumers;
6. zero-effect witnesses;
7. legacy Feature 047/048 non-authority and non-mutation.

## Focused local checks

```sh
python3 -m unittest \
  tests.test_hosting_image_trust \
  tests.test_hosting_image_contracts \
  tests.test_hosting_image_boundaries
python3 -m compileall -q sandbox/hosting/images sandbox/config/hosting_images.py
git diff --check
```

Status: passed locally on 2026-09-01 after the independent Sol High source-review
gate was clean.

- Focused Feature 049 selector: 37 tests passed.
- Non-opt-in hosting/config regressions in `tests.test_hosting`,
  `tests.test_config_facade`, `tests.test_config_descriptors`, and
  `tests.test_generic_compose`: 192 tests passed.
- `python3 -m compileall -q sandbox/hosting/images
  sandbox/config/hosting_images.py`: passed.
- `git diff --check`: passed.

The first focused run found that the legitimate outer receipt
`payload_digest` was rejected as payload authority, then found a valid plan was one
level deeper than the canonical input bound and one topology case belonged at its
owning input boundary. Each small repair received a clean Sol High exact-diff review
before the selector was rerun. The final counts above are the rerun evidence.

The post-implementation Sol High repair pass added exact immutable channel typing,
closed provenance, cumulative canonical traversal budgets, primary-project-file-only
config isolation for both schema kinds, fixed canonical vectors, and recursive
boundary/consumer mutations. Those repairs were source-reviewed only and do not alter
the unrun status above.

The final closure removes the trusted token type/issuer from public exports, requires
a private module capability for machine-boundary issuance, and makes provenance/build
identities digest-only with namespace-specific source grammars. Negative privacy and
ordinary-construction cases passed in the focused selector.

Every changed captured subprocess test elsewhere must use
`tests.subprocess_support.synthetic_environment`; Feature 049 itself launches no
subprocess.

## Acceptance boundary

Passing checks prove deterministic identity equality and effect-free refusal. They do
not prove artifact presence, registry availability/visibility (including the intended-
private declaration), manifest/config byte relationship,
publisher identity, signature validity, staging, runtime identity, remote health, or
production readiness.

The pre-source RED selector was intentionally not run under the implementation-order
override; T012 records that waiver and the expected causes without claiming observed
RED evidence. The user-required independent Sol High security/source review completed
with no critical, high, or medium finding; this was not a human review. Registry,
artifact, remote, staging, runtime, deployment, edge, and production evidence were not
attempted.

## Provision the v2 machine policy

Run `./sb host image provision --provision-phase machine-policy --project-dir PROJECT
--environment ENV --remote REMOTE --signed-receipt-directory RECEIPTS
--policy-authority-id ID --policy-revision REVISION --service-image-binding SERVICE=IMAGE
--activation-environment-binding IMAGE=ENV_VAR --rollback-public-key OWNER_ONLY_PUBLIC_KEY
--rollback-authority-id ID --rollback-authority-revision REVISION
--compose-provider-revision REVISION --confirm --json`, repeating both binding flags until
the `sandbox.hosting.yml` topology and receipt images are complete. Output is limited to
target, receipt/policy digests, dispositions, and installed owner-only paths.
