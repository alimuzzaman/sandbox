# Feature Specification: Controller Auto-Update

**Feature Branch**: `feat/controller-auto-update`

**Created**: 2026-11-10

**Status**: Draft

**Input**: User description: "The installed `sb` controller lags far behind what the remote servers run. When a remote the controller talks to runs a newer published Sandbox, the controller should catch up by itself: treat the remote's installed source revision only as a hint, verify it against the checkout's own published GitHub branch, fast-forward only when safe, refresh the checkout's setup, and re-run the original command once. Never update pinned controllers, machine-readable runs that would corrupt output, or durable-job children. Provide a config key and env var opt-out. The installed checkout should follow `latest`." Design approved by the owner on 2026-11-10.

## Clarifications

### Session 2026-11-10

Run without the owner present; each answer is the implementer's recommended default under the design approved on 2026-11-10 and is listed in the handoff for the owner to confirm or override.

- Q: No existing remote probe reports the installed git source revision (status reports only a content digest, and the local registration keeps only that digest). Where does the hint come from? → A: Two cheap sources, neither adding a remote round trip: (1) a successful install or migrate records the revision it installed in machine-local state shared by every checkout on the machine; (2) the install also leaves a small source receipt on the remote, which the existing service status probe reads inside its existing connection and records locally.
- Q: When does the update happen relative to the command that triggers it? → A: At the start of a remote-capable command, before any of its effects, using revisions learned earlier (by an install, a migrate, or an earlier status probe). A command never updates part way, so no effect runs twice.
- Q: Which commands trigger the check, and which remote's revision counts? → A: Commands that name a remote, plus remote-capable commands not run with an explicit local selection. The newest trusted revision recorded for any registered remote counts; trust comes only from the checkout's origin, so the remote's identity does not change the decision.
- Q: What do machine-readable runs print? → A: Nothing extra on either stream: no update, no notice. Standard output and the diagnostic stream stay exactly as without the feature.
- Q: How often does a refused hint cost a fetch and a notice? → A: A hint found not to be on the followed branch is remembered for 6 hours; within that window later commands neither fetch nor print for it. State-based skips (pinned, local changes, no upstream, lock held) print their notice only when the hint is already known locally to be a strict descendant, and never fetch.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Controller catches up with a newer published remote (Priority: P1)

The owner runs a remote command (for example a deploy or a remote status check) from the installed controller. The remote reports that it runs a Sandbox source revision that is newer than the controller's own revision, and that revision is part of the branch the controller checkout follows on its published origin. The controller fetches its own origin, fast-forwards to the verified revision, refreshes its setup if the dependency inputs changed, prints one line saying it updated from the old revision to the new one, and then runs the original command once on the new code.

**Why this priority**: This is the whole point of the feature: the owner stops running weeks-old controller code against current servers.

**Independent Test**: With a temporary origin repository, a controller clone one commit behind, and a remote revision hint naming the origin's newer commit, run a command that consults that hint; observe the clone at the newer commit, the update line, and the command's own output exactly once.

**Acceptance Scenarios**:

1. **Given** a controller checkout on a branch that tracks a published branch, clean, unlocked, and behind, **When** a remote command learns a remote revision that is a strict descendant of the checkout's current revision and is contained in the published branch after a fetch, **Then** the checkout fast-forwards to that revision (not past it), one update notice is printed, and the original command runs exactly once on the new code with its original arguments and exit status.
2. **Given** the same setup where the dependency inputs did not change between the two revisions, **When** the update runs, **Then** no dependency refresh is performed.
3. **Given** the same setup where the dependency inputs changed, **When** the update runs, **Then** the checkout's existing setup refresh runs before the command is re-run.
4. **Given** the remote revision equals the controller's revision or is older than it, **When** the command runs, **Then** nothing is fetched, nothing is printed, and the command runs normally.

---

### User Story 2 - A server can never choose what code the Mac runs (Priority: P1)

A remote reports a revision that is not on the published branch the controller follows (a private commit, a fork, a different branch, a forged value, or a revision the published origin does not have). The controller refuses to move, prints one plain line saying why, and runs the command on its current code.

**Why this priority**: The update path changes what code runs on the owner's machine. The remote is untrusted input; only the checkout's own configured origin is authority.

**Independent Test**: Hint the controller with a revision that exists only in a different repository, with a malformed value, and with a commit that exists on origin but on another branch; confirm the checkout never moves and each case prints one refusal line.

**Acceptance Scenarios**:

1. **Given** a hint revision that the published branch does not contain after fetching origin, **When** the command runs, **Then** the checkout does not move, a single notice states the revision is not on the followed branch, and the command runs on the current code.
2. **Given** a hint value that is not a well-formed full revision identifier, **When** the command runs, **Then** it is ignored without any fetch and the command runs normally.
3. **Given** a hint revision that is on the published branch but is not a descendant of the current revision (the checkout has local commits not on origin, or the histories diverged), **When** the command runs, **Then** the checkout does not move and one notice explains that only a fast-forward is allowed.
4. **Given** the fetch from origin fails (offline, authentication, or timeout), **When** the command runs, **Then** the checkout does not move, one notice says the check could not reach origin, and the command runs on the current code within a bounded delay.
5. **Given** any outcome, **Then** the controller never fetches from, or takes code from, the remote server itself.

---

### User Story 3 - Unsafe or pinned checkouts are left alone (Priority: P1)

The controller is a pinned checkout (detached on a specific revision, such as a per-revision controller worktree), or is on a branch with uncommitted changes, or another Sandbox process is already updating it. The controller does not update; it prints one line naming the reason and runs the command on the current code.

**Why this priority**: Pinned controllers exist on purpose, and an update must never destroy local work or race another process.

**Independent Test**: Repeat the P1 setup with a detached checkout, with a modified tracked file, with a staged change, with a branch that has no upstream, and with the update lock held by another process; confirm no movement and the matching notice in each case.

**Acceptance Scenarios**:

1. **Given** a detached checkout, **When** a newer hint arrives, **Then** it is not updated and the notice says the controller is pinned.
2. **Given** uncommitted changes in the working tree or index, **When** a newer hint arrives, **Then** it is not updated and the notice says the checkout has local changes.
3. **Given** a branch with no configured upstream, **When** a newer hint arrives, **Then** it is not updated and the notice says the checkout follows no published branch.
4. **Given** another process holds the update lock, **When** a newer hint arrives, **Then** it does not wait indefinitely, does not update, and the notice says another update is in progress.
5. **Given** a previous update crashed part way, **When** the next command runs, **Then** the update can be retried safely and the checkout ends either at the old revision or the new one, never in a half-moved state that blocks later commands.

---

### User Story 4 - A failed refresh rolls back (Priority: P2)

The fast-forward succeeds but the checkout's setup refresh fails (for example a dependency install error). The controller returns the checkout to the revision it had before, prints one line saying the update was rolled back and why, and runs the original command on the old code.

**Why this priority**: A broken refresh must never leave the owner with a controller that cannot start.

**Independent Test**: Force the refresh step to fail after the fast-forward; confirm the checkout is back at the previous revision, the notice is printed once, and the command runs on the old code.

**Acceptance Scenarios**:

1. **Given** the refresh fails after the fast-forward, **When** the update runs, **Then** the checkout returns to the previous revision, one rollback notice is printed, and the command runs once on the old code.
2. **Given** the rollback itself cannot complete, **When** the update runs, **Then** the notice states the checkout's current revision and the exact recovery command, and the command is not re-run on unknown code.

---

### User Story 5 - Owner can turn it off (Priority: P2)

The owner (or a CI host, or a test) disables auto-update with a machine config key or an environment variable. With it disabled, the controller never fetches or moves for this reason and prints nothing about it.

**Why this priority**: Every automatic code-change path needs an explicit off switch.

**Independent Test**: Run the P1 setup with the environment opt-out set, and separately with the config key set; confirm no fetch, no movement, no notice.

**Acceptance Scenarios**:

1. **Given** the environment opt-out is set to an off value, **When** a newer hint arrives, **Then** nothing is fetched or moved and nothing is printed about updating.
2. **Given** the machine config key is set to off and the environment variable is unset, **Then** the same holds.
3. **Given** the environment variable is set to on while the config key is off, **Then** the environment variable wins for that process.

---

### User Story 6 - Machine runs and job children keep exact output (Priority: P2)

A command runs with machine-readable output, or runs as the child of a durable job, or is the re-run that follows an update. The controller does not update in these runs, and its machine-readable output is byte-for-byte what it would have been without this feature.

**Why this priority**: Agents and the MCP server parse machine-readable output; a stray line breaks them. A durable job child must run the code its parent admitted.

**Independent Test**: Run a remote command with machine-readable output and a newer hint; compare standard output with the same run with the feature disabled.

**Acceptance Scenarios**:

1. **Given** machine-readable output was requested, **When** a newer hint arrives, **Then** no update happens and standard output is identical to a run with the feature disabled.
2. **Given** the process is a durable job child, **When** a newer hint arrives, **Then** no update happens and nothing is printed about it.
3. **Given** the process is the re-run that follows an update, **When** any hint arrives, **Then** no further update is attempted, so the command can never loop.

### Edge Cases

- The hint names a revision newer than the current published branch tip because origin has not been fetched yet: the fetch happens first, then containment is checked.
- The published branch has advanced past the hint: the checkout moves only to the hinted revision, never further.
- The checkout follows a local branch whose upstream is a differently named published branch (for example a local `installed` branch following `origin/latest`): the upstream is used, not the local name.
- The checkout's origin is not a GitHub repository, or the upstream remote is not named `origin`: the controller does not update and says why.
- Several remotes have recorded different revisions: the controller moves to the newest one that passes the trust rule; the others are ignored.
- The checkout is not a git repository at all (for example the staged runtime copy on a remote host): nothing happens and nothing is printed.
- A remote revision was recorded by a migrate from an unpublished feature branch: it is refused once after a fetch and then stays quiet for 6 hours.
- A command that never talks to a remote: no check happens.
- The command was started through a symlink (the installed `sb` on PATH): the re-run uses the same resolved entry point and the same arguments.
- The re-run itself fails: its exit status is the command's exit status; no rollback of the already verified update.
- Interrupt during fetch or fast-forward: the lock is released by process exit and the checkout is at one of the two revisions.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The controller MUST learn a remote's installed source revision only from (a) the revision a successful install or migrate on this machine installed, or (b) a source receipt the install leaves on the remote, read by the existing service status probe within its existing connection. It MUST NOT add a remote round trip for this feature.
- **FR-001a**: The check MUST run at the start of a remote-capable command (one that names a remote, or can run remotely and was not explicitly run locally), before any of the command's effects, and MUST NOT run for other commands.
- **FR-002**: The controller MUST consider updating only when the learned revision is a well-formed full revision identifier and is a strict descendant of the checkout's current revision. Equal, older, unrelated, or unknown revisions MUST cause no action and no output.
- **FR-003**: Before moving, the controller MUST fetch from the checkout's own configured origin and MUST NOT fetch from, or accept code from, the remote server.
- **FR-004**: The controller MUST require that the learned revision is contained in the published branch the checkout follows (its configured upstream on origin) after the fetch.
- **FR-005**: The controller MUST move only by fast-forward, and only to the learned revision.
- **FR-006**: The controller MUST NOT update when the checkout is detached, has no upstream, has uncommitted changes in the working tree or index, has an origin that is not GitHub, or when another process holds the update lock.
- **FR-007**: The update lock MUST be released when the holding process exits, including on crash, so the update is safe to retry.
- **FR-008**: When an update is skipped for a reason in FR-004, FR-005 or FR-006, or when the fetch fails, the controller MUST print at most one plain notice line to the diagnostic stream naming the reason, and then run the command on the current code. A hint refused after a fetch MUST be remembered for 6 hours, during which it causes no fetch and no notice. A state-based skip (FR-006) MUST NOT fetch and MUST print its notice only when the hint is already known locally to be a strict descendant of the current revision.
- **FR-009**: After a fast-forward, the controller MUST run the checkout's existing setup refresh when, and only when, its dependency inputs differ between the old and new revisions.
- **FR-010**: If the refresh fails, the controller MUST return the checkout to the previous revision, print one notice, and run the command on the previous code. If that return also fails, it MUST print the current revision and a recovery command and exit non-zero without re-running the command.
- **FR-011**: After a successful update, the controller MUST re-run the original command exactly once with the same entry point, arguments, working directory and environment, plus a guard marker that prevents any further update in that process tree.
- **FR-012**: The controller MUST NOT update when machine-readable output was requested, and MUST print nothing about updating on either stream; output MUST stay exactly as it would be with the feature disabled.
- **FR-013**: The controller MUST NOT update inside a durable job's child process.
- **FR-014**: The feature MUST be disabled by an environment variable set to an off value or by a machine config key set to off; the environment variable takes precedence over the config key. Default is on.
- **FR-015**: All fetch, inspection and refresh steps MUST be bounded in time so that a hung network or setup step cannot hang the command indefinitely.
- **FR-016**: Documentation MUST describe the trigger, the trust rule, the skip reasons, the opt-outs, the machine-readable output decision, and the one-time steps for making the installed checkout follow `latest`.
- **FR-017**: The feature MUST work for a checkout on a local branch whose upstream is a differently named published branch.

### Key Entities

- **Remote revision hint**: The installed source revision a remote reports for a remote command; untrusted input with a remote name and a revision identifier.
- **Controller checkout**: The repository the running `sb` entry point lives in; has a current revision, a branch or detached state, an upstream (remote and branch), and a cleanliness state.
- **Update decision**: The outcome of evaluating a hint: no action, skipped with a reason, updated, or rolled back.
- **Update lock**: A per-checkout exclusive marker held for the duration of a fetch, fast-forward and refresh.
- **Re-run guard**: A marker inherited by the re-run that forbids further updates.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a controlled test, a controller one or more commits behind reaches the hinted published revision after one remote command, with the original command run exactly once.
- **SC-002**: In 100% of tested untrusted-hint cases (not on the followed branch, malformed, diverged, other repository), the checkout revision is unchanged.
- **SC-003**: In 100% of tested unsafe-state cases (detached, dirty, staged, no upstream, lock held, opt-out set, machine output, job child, re-run), the checkout revision is unchanged and at most one notice line is printed.
- **SC-004**: Machine-readable standard output is byte-identical with the feature enabled and disabled in every tested machine-output run.
- **SC-005**: When the refresh fails, the checkout is back at its previous revision in 100% of tested cases.
- **SC-006**: A command with no newer hint pays no added network cost and no more than a negligible local delay (no fetch, no remote call).

## Assumptions

- Every checkout on one machine shares the same machine state, so a migrate run from one checkout (for example the `latest` worktree) is visible to the installed controller immediately. A revision installed from another machine is learned the next time a status probe reads the remote's source receipt; remotes installed before this feature have no receipt until their next migrate.
- Commands that cannot address a remote never trigger the check.
- Machine-readable runs never update and print nothing about it. Human runs also update when standard input is not a terminal.
- The setup refresh reuses the checkout's existing setup path; no new installer is introduced.
- The MCP server process does not self-update; only the command-line entry point does.
- Moving the owner's installed checkout onto `latest` is a one-time manual step the owner runs; this feature does not change which branch a checkout follows.
- Tests use temporary repositories only and never contact real remotes or the real origin.
