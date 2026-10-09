# Quickstart: validate remote development readiness

Prerequisite: a disposable remote with Docker, no `default-address-pools` in
`daemon.json`, and one hosted target running (a throwaway Compose project is
enough). Never use `xcloud-london` without owner approval; reaching any remote
follows the remote install protocol (migrate, repin, confirm
`runtime_revision_state: match`).

1. **Unit and contract tests**:
   `.cli-venv/bin/python -m unittest tests.test_remote_network_ranges tests.test_remote_network_program tests.test_remote_network_override tests.test_readiness tests.test_readiness_gate tests.test_remote_selection_refusals tests.test_docker_pool_plan_digest tests.test_architecture_boundaries`
   Expected: OK.
2. **Missing evidence**: `./sb remote readiness <remote> --json`. Expected:
   the capacity row is `not_ready` (`missing_pool_evidence`), with a proposed
   range and the assign command. `./sb test --remote <remote>` refuses with
   `remote_not_ready_capacity` and `bytes_transferred: 0`.
3. **Assign**: run the proposed `network-range assign ... --confirm`. Record
   the hosted container `StartedAt` values before and after. Expected: the
   range is recorded and the `StartedAt` values are unchanged.
4. **Run**: `./sb test --remote <remote>` (or `job-start`). Expected: the job
   is accepted, `network-range list` shows one allocation for the job's
   workspace, and the hosted containers are not restarted.
5. **Exhaustion and concurrency**: assign a range with capacity 1 (for
   example a /26 range with `--subnet-prefix 26`) on a second test project,
   then submit twice at once. Expected: one acceptance and one
   `docker_network_subnet_exhausted` with the allocation table and no
   subnets.
6. **Release**: `./sb workspace release <id>`. Expected: the allocation is
   gone and the next submission is accepted.
7. **Selection**: declare an unregistered remote in `sandbox.config.json` and
   run each submission path. Expected: `unknown_remote` with its source and
   the registered list, a non-zero exit, and no local run.
8. **Pool plan**: `./sb remote docker-pool <remote> --json`. Expected:
   `planned`, the hosted targets, the container count, and `plan_digest`.
   Start another container, then apply with the old digest. Expected:
   `docker_pool_plan_changed` and zero restarts.
