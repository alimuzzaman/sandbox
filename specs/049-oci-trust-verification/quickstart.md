# Quickstart: OCI Trust and Verification

This guide proves only pure local source behavior. It does not access a registry,
credential, Docker daemon, remote host, deployment, or production service.

## RED-first contract checks

Before implementation, add failing tests for:

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

Every changed captured subprocess test elsewhere must use
`tests.subprocess_support.synthetic_environment`; Feature 049 itself launches no
subprocess.

## Acceptance boundary

Passing checks prove deterministic identity equality and effect-free refusal. They do
not prove artifact presence, registry availability/visibility (including the intended-
private declaration), manifest/config byte relationship,
publisher identity, signature validity, staging, runtime identity, remote health, or
production readiness.
