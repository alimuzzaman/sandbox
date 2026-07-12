# Quickstart: Hermes Public Dashboard Access

This guide describes implementation and acceptance only. It does not authorize
Cloudflare, DNS, remote, service, or secret changes.

## 1. Local validation

```bash
python -m unittest tests.test_cloudflare_access tests.test_cloudflare_tunnel \
  tests.test_hermes tests.test_cli
python -m unittest discover -s tests
git diff --check
```

Expected: plans and protected-operation failures are sanitized and make no network or
remote mutation in mocked tests.

## 2. Read-only remote readiness

After current operator approval to inspect the remote:

```bash
./sb hermes dashboard doctor --remote scaleway-sandbox --json
./sb hermes dashboard expose --remote scaleway-sandbox \
  --fqdn hermes.asb.bd --plan --json
```

Expected: dashboard is loopback-only; the plan describes missing or conflicting Access,
tunnel, Caddy, DNS, secret-reference, and rollback prerequisites without changing them.

## 3. Separately approved public acceptance

Before confirmed apply, the operator must review exact identity policy/MFA/session,
secret references, tunnel ownership, Caddy fragment, DNS change, rollback, and emergency
containment. Then run only with current explicit approval:

```bash
./sb hermes dashboard expose --remote scaleway-sandbox \
  --fqdn hermes.asb.bd --confirm --json
```

Acceptance evidence must record, without credentials:

1. anonymous and unauthorized edge denial;
2. authorized MFA browser dashboard and interactive-session success;
3. no public Hermes listener and no direct-origin route;
4. healthy SSH-forwarded fallback;
5. a failed-route rollback and confirmed unexpose.

## 4. Emergency containment

If anonymous access is suspected, first disable the integration-owned tunnel route or
connector, then stop the Hermes dashboard only if needed. Preserve SSH access and the
Hermes CLI/gateway. Run `exposure-status` and follow its sanitized rollback guidance;
never print/reuse a connector token in a terminal transcript.

## Local implementation verification (2026-07-12)

The local implementation was verified without a Cloudflare, DNS, or remote mutation:

```bash
.cli-venv/bin/python -m unittest tests.test_cloudflare_access \
  tests.test_cloudflare_tunnel tests.test_hermes tests.test_cli -q
.cli-venv/bin/python -m unittest discover -s tests -q
git diff --check
```

Focused verification passed 114 tests. The full suite completed successfully with its
existing subprocess `ResourceWarning` output. Live acceptance remains pending explicit
approval for the Cloudflare account and `scaleway-sandbox`.
