# Rendered checkout lifetime

`host stack up --confirm` creates a detached worktree for the resolved commit.
Each new worktree under `runtime/stacks/<slug>/source/<id>` has a private bare
Git repository at `runtime/stacks/<slug>/source-git/<id>.git`. A local Git fetch
copies the requested commit and reachable objects into that repository. It does
not borrow objects through alternates or hardlinks from the caller checkout.
The backing repository pins the commit at `refs/sandbox/source` and owns the
linked worktree's Git administration. Retiring the materialized caller checkout
therefore does not retire the new rendered checkout's Git metadata.

The existing `worktrees` record determines which backing repositories must stay.
No new registry field or command is introduced. `checkout` continues to record
the caller context used for owner labels and merge sweeps. Private backing
repositories do not copy caller identity configuration or origin credentials.
Template provenance, requested ref, resolved commit, named ports, `uses`, and
manifest validation keep their existing meanings.

Creation uses an exclusive new backing directory. It never adopts a directory
left by a previous attempt. Before submission, a failed creation or manifest
validation cleans up its own pair when ownership can be established. An unknown
or refused submission retains the recorded worktrees and backing repositories.
On a later accepted submission, the existing cleanup removes earlier worktrees
and their private repositories. Legacy linked worktrees use the previous
record's caller checkout for cleanup, even when the new caller differs.
Stack teardown retains its existing destruction gate and removes local source
only after that gate succeeds. An interrupted cleanup may leave unused local
files; it must not remove backing metadata while its worktree still exists.

## Supported guarded orphan repair

Candidate source now implements `source.repair-rendered.v1`. It has not been
installed or exercised. The user's instruction to proceed without Spec Kit
supersedes the earlier proposal-only workflow for this assignment.

```text
sb host stack repair-source --remote REMOTE --name STACK --repair-source deployed --plan --json
sb host stack repair-source --remote REMOTE --name STACK --repair-source deployed --confirm --repair-plan-digest sha256:DIGEST --json
```

This is a separate synchronous installed-owner path, selected before controller
configuration. It requires joined authenticated Tailnet access and the current
owner capability, marker and revision. It stages no caller checkout, accepts no
arbitrary source path/argv, submits no durable job and reads no provider credential.
`deployed` explicitly selects the recorded target's canonical server repository as
an object supplier. Its current HEAD never substitutes for the attempt commit or
for the recorded rendered source path.

Planning requires all of these proofs:

- One exact remote/slug registry record and rendered manifest, with unchanged
  template path, requested ref, ref branch, full attempt commit, ports and uses.
  The recorded attempt commit can differ from the last delivered commit.
- The committed template rerenders to the existing manifest exactly. The selected
  source root is inside exactly one recorded rendered worktree. The object supplier
  contains that full commit in its own Git object store, without alternates. A live
  application alone is insufficient.
- Every tracked byte, executable bit and symlink target in the orphan agrees with
  that commit, with no missing or unexplained extra files/directories. Submodules,
  unsafe paths, checkout filters/encoding, selected credential-shaped filenames,
  excessive input and ambiguous content refuse. The original `.git` must be a
  regular dangling linked-worktree pointer into the recorded materialized caller.
- The exact hosting admission and terminal delivery agree on job, request, source
  commit, project/root/runtime identity and machine. Active or uncertain target
  ownership refuses. No old delivery is retired by this command.
- Native job evidence covers both the rendered source and its recorded caller.
  Every relevant consumer is terminal with durable stopped-descendant evidence,
  no remaining workspace/capacity lease, and complete process absence evidence.
  The original apply must be among those consumers. Missing retained evidence
  refuses; a timeout or terminal label alone does not release custody.
- Two complete same-owner process scans check command line, cwd and open descriptor
  targets, plus recorded PID/group absence. Denial, changed identity, incomplete
  enumeration or a target match refuses. No service-name exception, privilege
  fallback, process termination or lease recovery exists here.

The plan digest binds the current owner and boot, full registry record, exact
manifest bytes, tree, terminal delivery, source custody and original directory/Git
pointer identity. Planning creates no backing repository or replacement journal.
Git tree records and committed template bytes use a private bounded, lossless
reader; display-output redaction cannot alter the NUL-separated tree proof or
template contents. Validated template dependencies must match the recorded uses.
It takes existing serialization boundaries and may create their normal lock files;
it does not mutate jobs, leases, the manifest or workload data.

Confirmation revalidates the digest under the stack registry lock, target effect
and state locks, exclusive workspace-home maintenance lock, and a native job
transaction that excludes new admissions/lease changes. The native transaction
makes no row changes. The complete visibility check repeats before replacement.

The command copies the exact commit with depth one into a fresh private backing repository and
checks out a fresh staged worktree. Both are flushed before a journal authorizes
replacement. It renames the original orphan to a deterministic retained rollback
directory, then uses Git's worktree move/repair lifecycle to restore the original
recorded path. It never initializes the orphan or hand-edits its `.git` pointer.
Manifest and registry bytes, ports, copy identity, uses, workload data and delivery
history stay unchanged. Existing manifest/Git provenance validation must pass
before the journal says complete.

The journal records prepared, orphan-retained, installed and complete phases.
It also binds the created worktree, backing repository, Git administration and
pointer identities separately from the approved plan digest. Resumption refuses
substituted directories and older journals without those identities, retaining
the trees and journal for inspection. The worktree root keeps its identity when
Git moves it to the installed path.
Reconfirming the same plan can resume after publication of that journal, including
a directory move interrupted before Git updated its administration. Both original
and staged identity are rechecked before Git repair. No unjournaled directory is
adopted. A failure before journal publication can leave a private unused pair and
refuses automatic reuse; the original orphan has not moved. Failure after it moves
retains the backup and journal. Ambiguous layout or owner/revision drift refuses.
There is no automatic rollback deletion or garbage collection.

Successful output is a bounded receipt containing plan, manifest and tree digests,
commit and retained-orphan confirmation. A lost or failed confirmation response is
`repair_unconfirmed`, never success and never permission for a fresh plan, apply or
cleanup. Preserve the original plan. Request and response caps are 2048 and 4096
bytes. The receiver has a 120-second budget; the client has 135 seconds including
capability discovery and transport, with a 125-second repair transport cap.
Final manifest provenance checks use bounded Git reads under that same remaining
budget. Expiry before or after completion publication withholds a success reply
and preserves the existing journal for inspection.

This code cannot supply missing OS visibility or resurrect expired custody records.
Those are explicit runtime refusal conditions. It also does not release the separate
host-apply lease. The exact CFE recovery still needs its own complete fresh proof.

## Deferred verification

No runtime, test, import, build or syntax check accompanied the producer or repair source work.
After the authorized deployment-first review sequence, exercise these cases:

- Render from a disposable materialized checkout, retire that caller through
  its supported lifecycle, then validate the recorded manifest and read the
  exact rendered HEAD/content through the supported status path.
- Render successive commits with different callers. Verify each backing owns
  independent objects, an unknown submission retains every recorded pair, and
  accepted submission cleanup removes only superseded pairs.
- Fail local object transfer, worktree creation and manifest validation. Verify
  refusal, fresh-claim release and preservation of previous source/provenance.
  Interrupt creation and rerun without adopting leftover directories.
- Exercise legacy cleanup, nested source roots, down after successful destroy,
  failed destroy, and cleanup with mismatched or symlinked backing metadata.
  Verify no out-of-stack deletion and no surviving worktree loses its backing.
- Confirm a pre-existing orphan still refuses checkout validation unchanged.
  Repair plan mode must create no backing repository. Owner labels and merge-sweep
  repository selection must retain their prior behavior.

Focused regression tests belong with `tests/test_host_stack.py` and must use
the repository's synthetic subprocess environment. These are deferred cases,
not passing results or release acceptance.

Repair-specific deferred cases: wrong commit/content/mode/link, extra files,
replaced manifest or record, invalid original pointer, nonterminal/uncertain
custody, remaining lease, denied process reads, PID reuse, active target and
changed owner must refuse before replacement. Interrupt each journal/rename/Git
administration boundary and confirm only the original plan. Compare manifest and
registry bytes, source tree, ports, copy identity, uses, delivery and unrelated
job/lease records before and after. Verify the remote path with no caller checkout,
SSH key/agent, copied bearer or laptop server credential. No passing result is
claimed for any of these cases.
