# Nonsecret publisher proof

`sb publisher-proof --remote NAME --request-id ID --json` submits one retained
owner job. Use a clean reviewed source checkout and retain its original request
and returned job ID. An acceptance receipt is not a diagnostic result. Inspect
that job with `job-status` and bounded `job-output`; do not submit another ID
after unknown acceptance or an unconfirmed result.

The installed owner must advertise `job.publisher-proof.v1`. Capability and
current joined Tailnet owner checks precede source staging and job submission.
The receiver requires the exact retained command, request and origin remote.
There is no controller, SSH-shell, container-exec or fabricated-owner fallback.
The command registers through the command manifest and handles its invocation
before ordinary configuration, Compose and environment preparation.

The job has a 180-second budget. The existing owner process runner enforces its
remaining deadline and authority before and after each fixed publisher child.
The only cases are:

- Publish fixed public UTF-8 bytes containing a NUL and newline through the
  production `_write_remote_text` function, then verify the private file.
- Call that function again and verify identical bytes and file identity.
- Send a one-byte short frame to the unchanged publisher program, requiring
  exit 1, the exact `not_published` receipt and no destination file.

The recipient is beneath the retained owner's Sandbox home at
`publisher-canaries/<original-job-id>/`. The job directory must be new and
0700. Ancestors are opened without following symlinks and must be owned by root
or the owner without group/world write permission. The recipient is 0600,
single-link and owner-owned. Only fixed canary files are read. No existing
runtime, credential, provider, lease or application contents are inspected.
There are no caller-selected paths, payloads, shell commands or case options.

On Linux, ancestor traversal uses `O_PATH` descriptors. Search-only ancestors
such as root-owned `/home` at mode 0711 do not need directory-list permission.
The destination parent is opened readably through its checked descriptor for
fsync. Ownership, write-mode and no-symlink checks remain unchanged; no directory
permissions are widened. Platforms without `O_PATH` retain readable traversal.

Results contain fixed case names, booleans and allowlisted writer metadata.
Raw output, errors, paths and payloads are omitted. Failure stops later cases.
Uncertain execution preserves the existing owner completion fence. Files and
publisher locks remain for inspection; there is no automatic cleanup or retry.
Even a successful result proves only this new diagnostic job. It cannot
establish whether an older apply published its runtime files.

The initial installed diagnostic refused during ancestor preparation before
running a publisher case. The search-only traversal correction still requires
deployment and genuine-owner acceptance. Required acceptance is one
serialized genuine Linux owner job with all three cases complete, followed by
preservation checks for the original CFE v6 job/delivery/leases and workloads.
Local parser checks or synthetic owner tests do not establish that proof.
