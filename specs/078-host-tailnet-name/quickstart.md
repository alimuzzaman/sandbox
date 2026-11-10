# Quickstart: Tailnet-Only Hostnames

How to prove the feature works. Names and addresses are placeholders.

## Prerequisites

- A remote registered with `--front-door nginx`, with `tailscale_host`
  recorded (`sb remote join` or `provision` records it).
- `lego` installed on the controller (`brew install lego`).
- A Cloudflare API token with Zone:Read and DNS:Edit on the zone, stored in a
  file broker source (for example `sb secrets set --source personal --key
  EXAMPLE_CF_API_TOKEN`, entered by a human).
- In `$SANDBOX_HOME/sandbox.local.yml`:

  ```yaml
  tailnet_names:
    zones: [example.test]
    cloudflare_token: {source: personal, key: EXAMPLE_CF_API_TOKEN}
    acme_email: ops@example.test
  ```

## 1. Automated tests (fakes only)

Run on the test remote through a durable job, never on the controller:

```sh
sb job-start --remote <test-remote> --project-dir <worktree> --timeout 1800 -- \
  sh -c 'python3 -m unittest -v tests.test_host_tailnet_name tests.test_tailnet_name_cloudflare_child \
         tests.test_tailnet_name_remote_program 2>&1 | tail -60; rc=$?; exit $rc'
```

Expected: a `Ran N tests` line and `OK`. The suites cover the token-marker
sweep (SC-004), each failure point (SC-003) and a rerun with every step
reported as skipped (SC-002).

## 2. Live check on a throwaway name (only with the orchestrator's approval)

```sh
sb host tailnet-name add tn-probe.example.test --remote R --upstream 127.0.0.1:8025 \
  --acme-server letsencrypt-staging --json
```

Expected: `ok` true; steps `dns`, `certificate`, `install`, `server_block`
done; `verify_tailnet` reports an HTTP status; `verify_public` reports a
refused handshake. Then:

```sh
sb host tailnet-name add tn-probe.example.test --remote R --upstream 127.0.0.1:8025 --json   # every step skipped
sb host tailnet-name list --remote R --json                                                  # name, upstream, expiry
sb host tailnet-name remove tn-probe.example.test --remote R --json                          # block, cert, record removed
```

From a machine off the tailnet, `curl -v --resolve tn-probe.example.test:443:203.0.113.10
https://tn-probe.example.test/` must fail the handshake.

## 3. Secret hygiene spot check

After the live run, search the controller's Sandbox home and the job output
for the token's fixed prefix only through `sb secrets` tooling (never by
printing the token); the state file must hold only names, addresses, the record
id and the certificate expiry.
