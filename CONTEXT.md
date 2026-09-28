# Sandbox

Sandbox gives a project a repeatable place to develop, test, and run, locally or on a registered remote host. This glossary names the identities and outcomes that must stay distinct across those activities.

## Project and runtime

**Project**:
The application, plugin, or site whose source and declared runtime intent Sandbox manages. A project can have more than one instance.

**Project root**:
The selected source checkout that anchors a project's declaration and ownership. A second checkout is not automatically the same project owner.
_Avoid_: Instance, workspace

**Project identity**:
The identity of a project root together with its selected label. Local and remote activity can refer to that same identity, while an unadopted clone or relocation cannot assume it.

**Project kind**:
The project's declared runtime family, currently WordPress or generic Compose. It determines which operations are meaningful for that project.

**Instance**:
A separately addressable running or stopped runtime for a project, with its own state and lifecycle. Several instances can belong to one project root.
_Avoid_: Project, workspace

**Instance label**:
The name that distinguishes one instance selection from others under the same project root.

**Instance incarnation**:
One concrete existence of an instance. Recreating an instance can retain its name while creating a new incarnation.

**Remote host**:
A registered machine on which Sandbox can run project instances or durable work.
_Avoid_: Environment, workspace

**Workspace**:
A separately owned development source area used for durable work, especially on a remote host. Its identity and lifecycle are separate from an instance's.
_Avoid_: Instance, project root

**Workspace generation**:
One accepted or pending version of synchronized workspace source. A job can be bound to a particular accepted generation.

**Clean URL**:
A human-readable address for an instance, independent of its direct local port address.

## Work and evidence

**Job**:
A durable execution of project work, such as a command, test, or CI run, with an independently observable lifecycle and retained result.

**Job lifecycle**:
Whether a job has been accepted, is still active, or has reached a terminal result. A job's health observation does not itself make the job terminal.
_Avoid_: Job health

**Job health**:
Current evidence about whether a job is active, stalled, or unavailable. Missing or uncertain observation is not proof that the job stopped.
_Avoid_: Job lifecycle

**Request identity**:
The original identity of an attempted operation. A repeated observation or continuation must refer back to that attempt rather than silently becoming new work.

**Application revision**:
The selected version of the project's checkout bound to a job or delivery attempt. A nested application can yield a different source artifact for deployment.
_Avoid_: Sandbox control revision

**Source artifact**:
The exact application source selected for a hosted delivery. Its identity can differ from the enclosing checkout's application revision.

**Sandbox control revision**:
The version of Sandbox source that submits and interprets an operation. It is separate from the application revision and the installed controller revision.
_Avoid_: Application revision

**Installed controller revision**:
The version of Sandbox actually running on the selected controller. It may differ from the submitting Sandbox control revision.
_Avoid_: Sandbox control revision

## Hosting and delivery

**Hosted environment**:
A declared target for operating a project's public service, such as staging or production. It is selected separately from an instance label.
_Avoid_: Instance label, shell environment

**Source deploy**:
A one-way placement of selected project source on a remote host for an instance or preview. It is not a hosted environment promotion.
_Avoid_: Hosted apply

**Preview**:
A disposable instance made available for reviewing a selected project source outside its hosted environment.
_Avoid_: Hosted environment

**Hosted apply**:
An attempt to bring one hosted environment into its declared service, source, and route state.
_Avoid_: Source deploy

**Image activation**:
An attempt to make a staged, immutable application image the active release for a hosted environment.
_Avoid_: Instance wake

**Instance wake**:
Resuming a suspended instance so its existing address can serve a request.
_Avoid_: Image activation

**Delivery attempt**:
One attempt to place selected application source or an image on an exact hosted, deployed, or preview target. It has its own identity even when another attempt targets the same place.

**Delivery outcome**:
The recorded result and completeness of one delivery attempt. Command completion, current runtime health, and a prior successful attempt are separate facts.

**Public exposure**:
The requested public reachability of an instance, preview, or hosted service. A configured route alone does not establish that the intended application is reachable through it.

**Deployment trace**:
A diagnostic account of one full deployment command, including its early stages and related attempt results. It explains an attempt without granting authority to execute or recover it.

**Creation receipt**:
The owner's recorded relation between an operation and an exact instance incarnation, including whether that operation created or reused the instance.

**Recovery admission**:
The retained authority that binds a hosted apply to its original durable job before the apply can change its target.

**Delivery recovery**:
A separately requested continuation of an uncertain hosted delivery attempt using its original identity and retained evidence.
_Avoid_: Backup restore

## State recovery

**Instance snapshot**:
A named local WordPress state point for restoring an instance's database and, when included, uploads. It is a development rollback point rather than a portable backup.
_Avoid_: Recovery set, application revision

**Install baseline**:
The protected post-install WordPress database state used to reset an instance.
_Avoid_: Named instance snapshot

**Recovery profile**:
A declaration of valuable state to capture and the target to which that state can later be restored.

**Recovery set**:
A collection of captured artifacts for selected recovery profiles. It becomes restorable only after its completeness and integrity are verified.
_Avoid_: Instance snapshot

**Backup restore**:
Applying a complete recovery set to its declared target under a reviewed restore plan.
_Avoid_: Delivery recovery

## Product learning

**Feedback report**:
An observed incident, usability problem, or repeated task that may justify a reusable Sandbox capability. A report is evidence to review, not authority to change another resource.
