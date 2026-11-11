# Data model: Oldest-first durable-job admission

No schema change. The feature reads existing registry tables in a new order.

## Job (table `jobs`, existing)

| Field | Use here |
|---|---|
| `job_id` | Second order key (ties on `accepted_at`) |
| `accepted_at` | First order key, host-wide |
| `lifecycle` | Only `queued` jobs are admission candidates; `accepted`/`queued`/`running`/`cancelling` hold leases |
| `queue_reason` | `workspace_or_capacity_busy` marks a job waiting for a slot or its workspace; dependency, sync and policy reasons are kept as they are |
| `kind`, `parent_job_id` | Aggregates (`matrix`, `plan`, or rows with children) are never candidates |
| `target_kind`, `remote_name`, `project_identity`, `workspace_label` | Build the workspace namespace used to detect a lease on the job's own workspace |

## Workspace lease (table `workspace_leases`, existing)

A job is waiting on its own workspace when the admission sharing rule
(`workspace_compatible`) refuses it. Candidate definition: `contracts/admission.md`.

## Host slot (table `host_capacity_leases`, existing)

Held slots are counted against the current `max_parallel` (Part 1).

## Admission refusal kinds (on the exception as `.kind`, and per thread as `JobScheduler.last_refusal`)

| Kind | Meaning | Drain reaction |
|---|---|---|
| `admitted` | Another caller already admitted this job | continue; leave the job alone |
| `workspace` | The job's own workspace is busy | continue with the next job |
| `order` | An older eligible job is waiting | continue (the older job comes first in drain order) |
| `capacity` | No free slot | stop |
| `floor` | Host free memory or disk below the floor | stop |
| `policy` | `jobs.scheduler` invalid | stop |

The kind is not persisted and adds no payload key. The persisted `queue_reason`
values stay as they are (FR-014).

## State transitions

Unchanged. A drain moves `queued` to `running` through the existing launch path, or to
`failed` (`supervisor_launch_failed`) when a launch fails. The tick's reconcile moves
`running` to `interrupted` (`supervisor_lost`, `missing_supervisor_identity`,
`child_process_identity_mismatch`) only with existing proof.
