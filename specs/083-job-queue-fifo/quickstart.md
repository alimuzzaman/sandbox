# Quickstart: validate oldest-first admission

## Unit and concurrency tests

```bash
python3 -m unittest tests.test_job_queue_fifo tests.test_job_scheduler \
  tests.test_job_scheduler_policy tests.test_job_service tests.test_job_supervisor \
  tests.test_job_cancellation tests.test_remote_runtime_protocol_shapes
```

Expected: all pass. The FIFO module covers User Stories 1-4 and the 200-interleaving
concurrency check (SC-003).

## Remote run (before migrate)

Submit through the current controller as one job with `--cancel-on-stall`:

```bash
/Users/alim/Sites/git/.scratch/sandbox/controller-44bcdba/sb job-start --remote xspeed-dev \
  --project-dir . --workspace job-queue-fifo-tests --timeout 1500 --cancel-on-stall \
  --cwd-relative . --json -- python3 -m unittest tests.test_job_queue_fifo ...
```

## Live check after migrate (orchestrator)

1. `sb job-list --remote xspeed-dev --json`: `slots.max_parallel` is 4.
2. Queue five short jobs (`sleep 20`) on distinct workspaces with `--cancel-on-stall`,
   noting their acceptance order. Poll only the newest one every 30 s.
3. Expected: the first four start at once; the fifth starts within about 20-30 s of
   the first one finishing, with no other client reading status.
4. `job-status` of a queued job shows a `queue.position` that counts older
   startable jobs host-wide.
