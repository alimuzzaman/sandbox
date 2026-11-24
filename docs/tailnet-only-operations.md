# Operate a joined Tailnet controller

Use the selected remote's admitted Tailnet identity and installed server-owner
CLI for supported hosting, stack, preview and job operations. Keep the real
controller `HOME` and `SANDBOX_HOME`: these hold the existing registration,
operation receipts and target bindings. A laptop provider key, copied owner
bearer or private SSH key is not part of this execution path.

## Select the shipped client

The xspeed-dev release accepted on 2026-11-12 is
`2b911c3511288345daafa6e9c6054e6d909283ea`, runtime
`fb1c266560f26273e26a3c8d`, control protocol 25. It is published on the fork's
`staging/tailnet-only-release-20261112` branch. A floating `latest` or staging
tip is not this release.

The owner's normal `sb` entry is already pinned to that artifact. On another
controller, use a full-history checkout at the exact commit, with
`SANDBOX_AUTO_UPDATE=0` during version-bound work. A shallow source checkout can
fail receiver publication. Setup wrappers that verify Git HEAD need `SB` set to
the artifact's own `sb`, rather than an external launcher, and
`SB_RELEASE_REVISION` set to the same full commit.

## Preserve the registration

For an existing matching owner registration, the supported local transition is:

```sh
sb remote service refresh xspeed-dev --tailnet-only --adopt-compatible-revision --json
sb remote service status xspeed-dev --json
```

The transition verifies the saved machine/service/owner identity and compatible
installed service before updating the registration. It does not install a
service or change server policy. Inspect any requested historical binding change
with `sb remote rebind-host xspeed-dev --json`; apply only the explicit supported
plan. Preserve other aliases and their original jobs. Missing authority, identity
drift or an unknown effect is a refusal, not a reason to remove/rejoin records.

For a genuinely absent alias, use `remote join` with the intended Linux owner
and private Tailnet endpoint. Admission depends on the server's existing policy.
Supported owner commands do not fall back to a public address, ordinary SSH,
controller provider store or self-SSH. Lifecycle/bootstrap operations may still
use admitted keyless Tailscale SSH internally. This release is not permission to
remove an operator's recovery key or change SSH/Tailscale policy.

## Join when Tailscale SSH is refused

Control admission is the permission to join. When the server's tailnet SSH
policy refuses the selected Linux account, `remote join` still records the
registration and reports `ssh_access.state: denied` (or `check_required` for
Tailscale check mode), `ssh_check` (the exact `tailscale ssh USER@ADDRESS true`
to run) and `next_step`. A requested `--front-door nginx` is kept as
`front_door_requested`, apart from the proven `front_door`. The first lookup
after SSH works checks that SSH reaches the same machine and that nginx is the
incumbent, then records `front_door: nginx` and drops the request; no rejoin is
needed. An SSH machine-identity mismatch refuses and writes nothing, and an
observed Caddy is never pinned over the request.

Until SSH works:

- `remote service status` exits 0 with `status: control_only`,
  `data.ssh_access` (`state`, `check`) and a `next_step` naming the refusal.
- `job-status --remote` exits 1 with `tailnet_ssh_denied` (or
  `tailnet_ssh_check_required`) and the check, in JSON and human output.
- `job-start`, `job-output --remote`, `host stack` and `host apply` refuse with
  the same code and check.

Those commands run the installed `sb` over keyless Tailscale SSH; the control
service has no job or CLI route. The refusal is classified on the first SSH
call, usually the `$SANDBOX_HOME` lookup. A failed session's exit 255 and
"Permission denied" alone prove nothing, because OpenSSH passes on the remote
command's own exit status, so Sandbox concludes `tailnet_ssh_denied` only when
a bare `true` session is refused the same way. A server CLI request refused that
way is `not_accepted` only when its request id was generated for this call; a
request id you passed may belong to an earlier attempt whose receipt was lost,
so it stays `unknown` until SSH works and the original can be inspected. Check
mode never shows whether a command ran, so it is always `unknown`. A policy
that admits SSH for everyone who may join belongs in the owner's Tailscale
policy file, not in Sandbox.

## Follow the original remote job

A `server-cli` acceptance receipt with `job_id` means accepted, not completed.
Read the same remote job's terminal state and complete output:

```sh
sb job-status JOB_ID --remote xspeed-dev --json
sb job-output JOB_ID --remote xspeed-dev --stream stdout --profile full --json
```

Follow opaque output cursors through every page before parsing the command's
final JSON after progress lines. A confirmed stack-up can submit a nested apply;
that child's terminal delivery and readiness must also succeed. Parent exit 0
alone is insufficient. Unknown acceptance or observation retains the original
request/job; inspect that handle before any supported replay. Do not submit a
second request to bypass uncertainty.

Joined `host exec --remote R --name S --service SERVICE -- ARGV` needs no local
hosting history. Default batch output returns verified stdout/stderr bytes and
the underlying exit code, including intentional nonzero exits. JSON is a text
projection; private capture artifacts preserve original bytes. Keep a finite
deadline; output is bounded, not interactive/live streaming. Recovery inspects
the exact original job and lease; a lease is never removed merely because a
caller timed out. See [owner execution](hosting-server-owner-execution.md).

## Recognize release limits

- The accepted release proved normal pinned-client execution, exact stdout and
  stderr, intentional exit 7 and same-job artifact retrieval. Six read-only
  command families were observed on predecessor `2e05f010`; they were not rerun
  or relabelled as final-commit results.
- Destructive/provider mutations and recovery confirmation were source-reviewed
  but not exercised by that acceptance. Runtime compatibility is not proof that
  every command or a later fix is installed.
- The xspeed-dev nginx backend lacks preview/instance route support. Preview
  creation and `deploy --expose` refusal there is a backend limit. Image signing
  retains its independent configured authority.
- Generic job capability discovery at this pin can reject the advertised
  `preview.owner-cli.v1` contract. Its narrow client alignment is pending
  publication; this refusal is not evidence the server needs reinstalling.
- Secret reconciliation and atomic owner-path delivery remain separate P1
  work. A missing owner-plan selector does not prove every server store is empty.
  Preserve existing passwords and encryption seeds; use broker metadata and the
  separately authorized reconciliation plan. Tailnet or host-exec success is not
  proof of safe secret delivery or permission to move a real key.
