# Quickstart: Host Exec

Prerequisites: a registered, provisioned remote `R` and a target this
controller applied there (plain `P/E`, or a stack from `sb host stack up`).

1. Exec with stdin and exit status:

   ```sh
   printf 'canary-%s' "$(date +%s)" | ./sb host exec --remote R \
     --target P/E --service S -- sh -c 'id -u; head -c 8 >/dev/null; echo ok'
   echo "exit=$?"
   ```

   Expect the service user's uid, `ok`, `exit=0`.

2. Failure propagates:

   ```sh
   ./sb host exec --remote R --target P/E --service S --json -- sh -c 'exit 3'
   ```

   Expect `"ok": false`, `"exit_code": 3` and exit status 3.

3. Refusals: an unknown target gives `host_target_unknown`, an unknown service
   `host_exec_unknown_service` with the service list.

4. Stack read-only commands from a template checkout:

   ```sh
   ./sb host logs --remote R --target <stack project>/<env> --lines 50
   ```

   Expect logs, not `stack_template_requires_stack_up`.

5. Canary audit: `grep -r <canary> $SANDBOX_HOME/runtime` finds nothing.

Unit coverage: `tests/test_host_exec.py`, `tests/test_host_target_resolution.py`.
