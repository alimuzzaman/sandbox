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

## 5. Recover a current-contract failed apply without replaying it

`sb host recover` requires an explicit remote, project, environment, terminal failed
durable job, original request, distinct recovery request, and the generation reported
by `host status --json`. The supervisor supplies fixed job/request/project/source
identity without enumerating or copying the parent environment. Eligible applies bind
that identity before the first hosting effect.

Recovery validates the canonical job snapshot and exact `host apply --confirm` argv,
clean source, project, target, and pre-effect receipt before remote observation. One
bounded observer captures start/end runtime inventory epochs, deployed config-file
digests, immutable image IDs, configured/running topology, source revision, health, and
the apply-owned one-shot phase receipt. Apply and recovery share a target lock and
reload the generation under it. Attempts are bounded; compacted request tombstones stay
non-reusable.
Target identity binds the normalized registered SSH endpoint, control transport and URL,
Tailscale host, MCP port, remote name, and runtime home; bearer credentials are excluded.
That endpoint identity is not machine authority. Eligible apply also records Feature 046's
authenticated stable `target_identity`, and recovery measures it before and after the
bounded runtime observation. Missing, legacy, rebuilt, or repointed identity refuses.
The authenticated controller derives that opaque identity from the machine ID, never a
hostname or endpoint string; invalid or unavailable machine ID makes the projection fail.
After target ownership, recovery resolves the registration again and holds the shared
registration guard through durable commit; supported re-registration uses the same guard.
An apply can continue without Feature 046 identity, but it mints no recovery authority.
Apply also recomputes its complete registration-derived plan, DNS records, origin checks,
and Cloudflare preconditions from the guarded entry. Original authority stores the
canonical non-secret edge intent and digest. Observation and pre-edge authority compare
that exact intent, and the edge adapter consumes its bound records rather than recomputing
from an unguarded entry. A same-machine origin change therefore refuses before effects.
The intent also binds certificate hostnames and supplies them to Origin CA issuance. It is
limited to 64 routes, 128 DNS records, 64 unique certificate hostnames, and 64 KiB; the
complete persisted hosting operation is limited to 128 KiB. Overflow creates no recovery
authority, binding key, or metadata directory. A new key/version is prepared in memory and
the exact prospective full envelope is bounded before either is published.
The recovery-only command predispatch policy skips compatibility migration, finalization,
Compose, and environment writers before the observer can refuse.

Secret equality comes from separate owner-only broker metadata produced during an
eligible apply. Recovery reads no secret source or value. Its opaque metadata ID and
secret-file epoch plus key identity must remain exact; environment-backed or stale metadata
refuses. The selected secret source and binding key must each be a real, owner-only regular
file; missing files and symbolic links are never authorizing.
The target lock precedes the broker revision lock, which covers metadata validation
through durable recovery commit. `environment.env`'s raw SHA-256 never enters a receipt;
it is transformed into an owner-keyed opaque HMAC identity first.
The finite broker guard also holds the same per-source `.<name>.sb-secrets.lock` as the
generic secret writer. Apply reads/caches secrets, computes its keyed config identity,
writes metadata, and accepts durable authority without releasing that transaction.
Legacy/job/source ineligibility is decided before broker acquisition.

Pre-effect apply authority and direct-apply revocation use the atomic recovery writer,
including file and parent-directory fsync. Sync watch uses a target effect lease plus a
short state transaction rather than retaining the shared state lock for its full run.
Every state mutation in locked apply, including init completion, observation refresh,
runtime reconciliation, and final edge success, uses that durable writer. First key
publication, newly created authority directories, and broker metadata replacement fsync
the required parent-directory entries before host authority can rely on them.
The bounded login URL writer also commits its receipt through the same durable repository
writer while holding its existing target/state lock.
Recovery writes a bounded `authorizing: false` provisional marker, immediately observes again
under ownership, then uses a separate atomic commit to promote matching evidence. The marker
has no receipt, generation advance, terminal success, or edge reachability. Only the same
observation request/digest may resume from `observation_pending`; the same provisional owner
may resume only its post-write observation. Edge entry is persisted before adapter invocation;
malformed or effect-entered state stays fenced. Exact edge replay returns its recorded terminal
edge result and does not use the observation-only `already_reconciled` alias.
Recovery lock directories stay exact `0700`, but a trusted controller-owned, non-writable
runtime parent may remain `0755`; the state file is exact owner-only regular `0600`. Parent
components are walked before lock creation so user-controlled symlinks produce no child.
The shared registration lock uses a canonical owner-only directory and rejects directory
or file symlinks, unsafe ownership/mode, non-regular files, and multiply linked files.

Observation success reconciles only local receipt state. Confirmed edge continuation
is a distinct request and needs an authorizing governance projection, a successful
observation reference, unchanged evidence/generation, and edge as the sole pending
phase. Its adapter can reach only existing Caddy, Cloudflare record/certificate, and
edge verification helpers. Unknown edge outcome is fenced against every later request.
Feature 047 has no implemented authorizing projection yet, so the public edge path is
currently unreachable and returns `governance_unavailable`; only the bounded adapter
seam is present and covered by an authorized synthetic fixture.
Local tests do not prove an installed remote runtime or Lenzora deployment.
