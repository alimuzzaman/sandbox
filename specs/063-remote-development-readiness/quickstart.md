# Quickstart: validate remote development readiness

Prerequisite: a disposable remote with Docker, no `default-address-pools` in
`daemon.json`, and one hosted target running (a throwaway Compose project is
enough). Never use `xcloud-london` without owner approval; reaching any remote
follows the remote install protocol (migrate, repin, confirm
`runtime_revision_state: match`).

1. **Unit and contract tests**:
   `.cli-venv/bin/python -m unittest tests.test_remote_network_ranges tests.test_remote_network_program tests.test_remote_network_override tests.test_readiness tests.test_readiness_gate tests.test_remote_selection_refusals tests.test_docker_pool_plan_digest tests.test_resource_network_capacity tests.test_workspace_runtime tests.test_remote_docker_pool_capacity tests.test_doctor_remote_targets tests.test_architecture_boundaries`
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
5. **Exhaustion and concurrency**: on a second disposable remote with no
   daemon pool and no other range, assign a capacity-1 range (a /24 with
   `--subnet-prefix 24`), then submit twice at once from two workspaces. Expected: one acceptance and one
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
