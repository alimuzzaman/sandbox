# Quickstart: validating 082

## Unit tests (remote, never on the owner's Mac)

```bash
sb job-start --remote xspeed-dev --timeout 1800 -- \
  .venv/bin/python -m unittest tests.test_local_runtime tests.test_proxy_on_demand \
  tests.test_setup_idempotency tests.test_clean_url_default_policy \
  tests.test_clean_url_provider tests.test_sandbox
```

Compare with the same command on an `origin/latest` checkout.

## Live scenarios (on a disposable Docker controller)

1. `sb ensure --create` an instance with a `.tst` domain; `docker ps` shows the proxy.
2. `sb down`: the proxy stops; `docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' sandbox-proxy`
   prints `no`.
3. Restart the engine: no Sandbox container runs.
4. `sb up`: the proxy runs and the instance URL is `http://<name>.tst`.
5. Start two instances, `sb down` one: the proxy keeps running.

## Remote-only

1. Set `controller: {local_runtime: none}` in `$SANDBOX_HOME/sandbox.local.yml`.
2. `PATH` without `docker`: `sb doctor` passes with the remote-only row;
   `sb job-start --remote <r> -- true` works; `sb up` in a Docker project refuses with
   the switch message.
