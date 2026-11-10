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
2. Queue five jobs of `sleep 30` on distinct workspaces with `--cancel-on-stall`, in a
   known order, and read no status while they run.
3. After they finish, read each job's `accepted_at`, `started_at`, `finished_at`.
4. Expected: jobs 1-4 start at once. Job 5 starts within 5 seconds of the first of 1-4
   finishing (release-driven drain from that job's supervisor), and in every case within
   30 seconds (the service tick).
5. Repeat step 2 while polling only job 5 every 10 seconds: job 5 still starts no
   earlier than the first finish (it never jumps ahead of 1-4).
6. A queued job's `queue.position` counts older candidates host-wide.
