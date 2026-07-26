# Remote hosting and development boundaries

The remote features share a VPS but serve four distinct workflows. Keeping the
boundaries explicit prevents a test job, a source snapshot, or an MCP connection
from being mistaken for a production change.

## 1. Source deploy: make a current checkout available remotely

```sh
./sb deploy --project-dir /path/to/project --remote NAME
./sb deploy --project-dir /path/to/project --remote NAME --ensure --expose
```

`deploy` is a one-way, on-demand source snapshot. It pushes the current commit and
supported local changes to the remote checkout; a later deploy replaces that snapshot.
`--ensure --expose` may boot a sandbox instance and publish its requested sandbox URL.
It does not watch files, synchronize continuously, create a durable development job,
or promote the checkout to a declared production environment.

## 2. Remote development jobs: run and recover bounded work

```sh
./sb test --remote NAME --workspace node-unit --timeout 1800 --detach -- npm test
./sb job-status JOB_ID --remote NAME --json
./sb job-output JOB_ID --remote NAME --cursor 0 --max-bytes 65536
```

Remote test, `exec`, E2E, matrix, and compatible CI commands use the durable job
runtime. The selected source is deployed to the named development workspace before
the supervisor accepts the job. The supervisor owns process pipes and retained output,
so a caller can reconnect with the job ID and cursor after SSH, terminal, or MCP
disconnection. Workspace cleanup remains explicit.

These commands are for development and verification. They do not update a hosting
manifest, DNS, Caddy configuration, or production secrets.

## 3. Remote MCP: use the co-located control plane

After `./sb remote provision NAME --confirm`, register the separate `sandbox-NAME`
MCP server with the client through its supported secret mechanism. For live remote
work, prefer that co-located server to a long-lived SSH command: submit or inspect
work with durable job APIs, then page retained status/output by job ID and cursor.

The remote MCP server is a control-plane endpoint. It neither watches local files nor
turns a job request into a production deployment. Its credential is managed outside
Git and must never be placed in command output, source, or an MCP tool argument.

## 4. Production hosting: apply a declared, reviewed environment

```sh
./sb host validate --project-dir /path/to/site
./sb host plan --project-dir /path/to/site --environment production --remote NAME
./sb host apply --project-dir /path/to/site --environment production --remote NAME --confirm
```

Production hosting is intentionally a separate workflow. `validate` and `plan` are
offline/read-only preparation; `apply --confirm` is the explicit action that transfers
the approved checkout, runs declared health checks, and changes only the hosting
manifest's Caddy, DNS, and secret mappings. It must not be inferred from `deploy`, a
remote test, CI completion, an exposed sandbox URL, or an MCP request.

Use the environment's configured branch and clean-tree policy before applying. Treat
production credentials and one-time login URLs as secrets, and keep each public change
within the declared manifest scope.
