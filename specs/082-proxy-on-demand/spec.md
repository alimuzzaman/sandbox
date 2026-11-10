# Feature Specification: Clean-URL proxy on demand and remote-only controllers

**Feature Branch**: `fix/proxy-on-demand`
**Created**: 2026-11-10
**Status**: Draft
**Input**: User description: "082-proxy-on-demand: the sandbox-proxy Caddy container runs only while a local Docker instance needs it, and remote-only or Docker-less controllers never need local Docker." Owner request (2026-11-10): "why do sandbox need [sandbox-proxy] container to run? check how to fix it and run without docker for remote only or non docker setup". Feedback record `707ed7b913b0aada374ad19beb223e10`.

## Background

The default clean-URL provider (`sandbox-caddy`) is one Caddy container that routes
`http://<instance>.<tld>` names on a dedicated loopback address to local Docker
WordPress instances. It is created with a restart policy that brings it back every
time the container engine starts, even when every local instance is stopped. Local
instances have no such policy, so after an engine restart the proxy is the only Sandbox
container running. On the owner's machine all seven local instances were exited while
the proxy was up. The proxy has no work to do in that state: starting an instance
already brings the proxy back, and instance URLs fall back to the per-port address
while it is down.

Some controllers are used only to drive remote servers, and some have no container
engine at all. Those controllers should never need a local container.

This feature does not change which clean-URL provider is the default (constitution
principle VI, `docs/clean-url-default.md`). The Docker/Caddy proxy stays the default
provider; it only stops running when nothing on the machine needs it.

## Clarifications

### Session 2026-11-10

Resolved by the implementing agent from the owner's brief (no interactive answers were
collected; the brief allows either switch and asks only for the cleanest one).

- Q: Which machine-level switch declares a remote-only controller: a new local-runtime
  key, or the existing `disabled` clean-URL selection? → A: A new local-runtime key
  (`docker` default, `none`), with an environment override. `disabled` keeps its
  narrower meaning: local Docker instances still run, on per-port URLs, and the proxy
  is never started for them.
- Q: Does a missing container engine, without the switch, make doctor fail? → A: Doctor
  keeps reporting it for a default (local Docker) machine; with the switch set it is
  informational only.
- Q: What do listing and status commands show for local Docker instances while the
  switch is set? → A: The registry rows, shown as not running, with no engine call.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The proxy runs only while something needs it (Priority: P1)

A developer with local Docker instances stops their last running instance, or restarts
their container engine with every instance stopped. The clean-URL proxy is not left
running on its own. When they start any local Docker instance again, the proxy comes
back and the clean URL works as it does today.

**Why this priority**: It is the reported problem and affects every default install.

**Independent Test**: With one local Docker instance, start it (proxy running), stop it
(proxy stopped), restart the container engine (proxy still stopped), start it again
(proxy running, clean URL serves).

**Acceptance Scenarios**:

1. **Given** the proxy is running and exactly one local Docker instance is running,
   **When** that instance is stopped or destroyed, **Then** the proxy is stopped.
2. **Given** two local Docker instances are running, **When** one is stopped,
   **Then** the proxy keeps running.
3. **Given** the proxy and all instances are stopped, **When** the container engine
   restarts, **Then** the proxy stays stopped.
4. **Given** the proxy is stopped and clean URLs were set up before, **When** a local
   Docker instance starts, **Then** the proxy starts and the instance's clean URL
   serves.
5. **Given** an existing install whose proxy still carries the old self-restarting
   policy, **When** Sandbox next applies the proxy configuration, **Then** the
   container carries the new policy.
6. **Given** the stop step already ran (proxy stopped, nothing running), **When** it
   runs again, **Then** nothing changes and no error is reported.
7. **Given** an instance is idle-stopped with wake-on-request enabled, **When** the
   last running instance stops, **Then** the proxy keeps running so a request can
   still wake that instance.

---

### User Story 2 - A remote-only or Docker-less controller never needs local Docker (Priority: P1)

An operator uses a laptop only to run jobs and host stacks on remote servers, or works
on a machine with no container engine. Every remote command works without a local
container engine and prints no warning or error about it. The operator can declare the
machine remote-only; local Docker instance commands then refuse with a clear message
that names the switch, instead of trying to reach Docker.

**Why this priority**: The owner asked for it directly; without it the fix only
reduces the symptom on Docker machines.

**Independent Test**: On a controller with no container engine on its command path,
run the remote command set (job submission and status, host apply, stack commands,
remote resources, the web panel, the MCP server start, doctor, update) and confirm none
of them starts a local container, fails because the engine is missing, or warns about
it.

**Acceptance Scenarios**:

1. **Given** a controller without a container engine, **When** the operator submits a
   remote job and reads its status and output, **Then** each command succeeds and
   prints nothing about the missing engine.
2. **Given** the remote-only switch is set, **When** the operator tries to create or
   start a local Docker instance, **Then** the command refuses before any engine call
   and names the switch and how to clear it.
3. **Given** the remote-only switch is set, **When** the operator works with a Herd or
   Valet instance, **Then** it works as before and the Docker proxy is never started.
4. **Given** the remote-only switch is set, **When** the operator runs doctor,
   **Then** doctor reports the mode and does not count a missing engine as a problem.
5. **Given** the remote-only switch is set and an old proxy container is still running
   from before, **When** the operator runs the existing proxy stop command, **Then** the
   proxy stops and stays stopped across engine restarts.
6. **Given** clean URLs are disabled for the machine, **When** a local Docker
   instance starts, **Then** the proxy is not started and the instance uses its
   per-port URL.

---

### User Story 3 - The docs say why the proxy exists and how to go remote-only (Priority: P2)

A new user reads the setup section and the clean-URL page and learns what the proxy is
for, when it runs, and how to run Sandbox without local Docker.

**Why this priority**: The owner asked "why"; the answer belongs in the docs.

**Independent Test**: A reader can find, in the README setup section and the clean-URL
page, the proxy's purpose, its on-demand lifetime, and the exact remote-only switch.

**Acceptance Scenarios**:

1. **Given** the README setup section, **When** a reader looks for remote-only setup,
   **Then** they find the switch and a link to the clean-URL page.
2. **Given** the clean-URL page, **When** a reader looks for when the proxy runs,
   **Then** they find the on-demand rule and what keeps it running.

### Edge Cases

- The container engine is missing or not responding when the stop step runs: the step
  does nothing and reports nothing, and it never treats an unknown state as "idle".
- Two lifecycle commands run at once, one starting an instance and one stopping the
  last other instance: the proxy must end up running.
- The running-instance check cannot read the instance list: the proxy is left as is.
- A user started the proxy by hand with `sb domains up` while no instance runs: it may
  be stopped the next time an instance stops; it is not restarted automatically.
- The proxy is running for an incumbent-adoption setup or another product: Sandbox only
  ever stops its own proxy container.
- A remote-only switch value Sandbox does not recognise: treated as an error with a
  clear message, not silently as local Docker.
- The switch is set while local Docker instances still exist in the registry: they are
  listed by status commands but cannot be started until the switch is cleared.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The proxy container MUST NOT be configured to restart on its own when the
  container engine starts.
- **FR-002**: Starting or ensuring a local Docker instance MUST bring the proxy up when
  clean URLs were set up and the default provider is selected, as today.
- **FR-003**: After a local Docker instance is stopped or destroyed through Sandbox,
  Sandbox MUST stop its proxy when no local Docker instance is running and no
  wake-on-request route exists, and MUST leave it running otherwise.
- **FR-004**: The stop decision MUST fail safe: if the engine, the instance list or the
  running state cannot be read, the proxy MUST be left as it is.
- **FR-005**: The stop step MUST be safe to rerun and MUST be serialised with proxy
  start so a concurrent instance start always ends with the proxy running.
- **FR-006**: On the next proxy apply, an existing proxy container with the old policy
  MUST be recreated or updated to the new policy.
- **FR-007**: The selection `disabled` for clean URLs MUST keep the proxy from being
  started by instance lifecycle and MUST count as "nothing needs the proxy" for the
  stop step.
- **FR-008**: Sandbox MUST offer one machine-level switch that declares the machine has
  no local container runtime (remote-only). Its default is the current behaviour.
- **FR-009**: With the switch set, every local Docker instance command MUST refuse
  before any engine call, with a message naming the switch and how to clear it. Herd
  and Valet instances MUST keep working and MUST NOT start the Docker proxy.
- **FR-010**: Remote commands (job submission, status and output; host apply; stack
  commands; remote resource commands; the web panel; MCP server start; update and
  controller auto-update) MUST NOT start a local container, and MUST NOT fail or warn
  because a local engine is missing, with or without the switch.
- **FR-011**: Doctor MUST report the local-runtime mode, and with the switch set MUST
  NOT count a missing or stopped engine as a problem.
- **FR-012**: The README setup section and the clean-URL docs MUST say why the proxy
  exists, when it runs, and how to run remote-only.
- **FR-013**: With the switch set, listing and status commands MUST show local Docker
  instance rows from the registry, as not running, without calling the engine.

### Key Entities

- **Proxy demand**: whether anything on this machine needs the proxy now: running local
  Docker instances, plus instances with a wake-on-request route. Unknown is not zero.
- **Local runtime mode**: machine-level setting, `docker` (default) or `none`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After stopping the last running local instance, zero Sandbox containers
  are left running on the machine (absent wake-on-request routes).
- **SC-002**: After a container engine restart with all instances stopped, zero Sandbox
  containers start on their own.
- **SC-003**: Starting a stopped local instance gives a working clean URL with no extra
  step, as before.
- **SC-004**: On a controller without a container engine, every remote command in
  FR-010 completes with zero engine-related errors or warnings.
- **SC-005**: With the remote-only switch set, 100% of local Docker instance commands
  refuse with a message naming the switch, and none reaches the engine.

## Assumptions

- "Local instance" means a Docker Compose instance managed by this controller; Herd
  and Valet instances never use the Docker proxy.
- An instance idle-stopped with wake-on-request needs the proxy to receive the waking
  request, so it counts as demand.
- The stop step runs after Sandbox's own stop and destroy commands. Instances stopped
  outside Sandbox (engine UI, `docker stop`) are picked up on the next Sandbox stop,
  destroy or proxy apply; the new restart policy covers the engine-restart case.
- Workspace reaping and resource cleanup live in files other work is changing; they are
  not wired to the stop step in this feature, and a later stop or proxy apply catches
  up.
- The remote-only switch lives in the machine-local configuration, with an environment
  variable override, following the existing clean-URL selection precedence.
- Root DNS, the local CA and the privileged helper are not changed or removed.
