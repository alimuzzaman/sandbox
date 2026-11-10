# Quickstart: Host Stack Templates

## Prerequisites

- A checkout whose committed `sandbox.hosting.yml` has a `stack:` block (see
  `docs/remote-hosting.md`, "Stack templates").
- A registered nginx-front-door remote `R`.

## Validate without changing anything

```sh
./sb host stack up --template ~/src/hub/sandbox.hosting.yml --name demo \
  --ref stack/demo --remote R --plan --json
```

Expect `ok: true`, project `xspeed-hub-demo`, hostnames `hub-demo.example.test`,
`admin-demo.example.test`, `renderer-demo.example.test`, a `mailpit` port in
18100-18199, and the `submission` argv. `git worktree list`, `runtime/stacks/` and
`sb host stack list --remote R` are unchanged.

## Refusals to check

- `./sb host apply --project-dir ~/src/hub --remote R --confirm` → `stack_template_requires_stack_up`.
- `--ref <sha>` with `allowed_branches: ['{ref_branch}']` → `stack_branch_unavailable`.
- A template whose hostname another project holds on R → `hostname_owned_by_other_project`.

## Bring up, list, tear down (owner-authorized only)

```sh
./sb host stack up --template ~/src/hub/sandbox.hosting.yml --name demo --ref stack/demo --remote R --confirm
./sb job-status <job_id>
./sb host stack list --remote R
./sb host stack down --name demo --remote R --confirm
```
