# Protected runtime environment facility v1

Source contract only. This does not claim installed capability, actual image
compatibility, successful runtime consumption or release approval. Human review
must approve credential custody and the production execution path before release.

`sandbox.hosting.shared_credentials.application` exports `RuntimeAction` and
`RuntimeEnvironmentConsumer`. A server-owned measured action contains an absolute
`compose_file` Path, its SHA-256 `compose_digest`, `compose_project`, `service`,
digest-pinned `image`, absolute Unix `engine_socket` Path, exact `slot_names`
tuple, `owner_uid` and absolute Unix `original_deadline`. No wire command,
callback, shell, executable selection or credential value is an action field.
The resolver must bind this action to the registered source/artifact and target.

The frozen consumer binds `intent_digest`, action and `consumer_id`. Its fixed
revision is `runtime-env-v1` and purpose is `runtime-environment`. `matches`
compares the exact intent digest, consumer mapping destination set, count,
revision, purpose and provider/generated mapping kinds. `consume` accepts only
an exact dict of nonempty NUL-free strings for those slots. It does not acquire
provider credentials, generate replacements or grant generation authority.
The parent supplies separately authorized generated slots inside the real effect.

Before delivery and after use the consumer resolves the concrete current
checkpoint through `service.require_current_consumer_effect`, compares its
identity and invokes `require_current`. There is no injected authority callback.
The process timeout is bounded by both the action's original deadline and the
checkpoint's retained `effective_deadline`; completion rechecks the deadline.
The parent remains responsible for recording checkpoint completion. Any started
failure or authority uncertainty returns `effects_unknown`, never permission to
redispatch. Before-start refusal returns `failed`.

The genuine owner API is `sandbox.hosting.endpoints.local.owner_local` with
`require_current(owner, engine=True)`. UID, canonical home containment and exact
policy socket must agree. The existing `_require_local_docker_runtime` choke
point remains mandatory. Missing sibling APIs refuse before a child starts.
The Unix socket must deny world access. Existing Docker-group members already
have full daemon/container-inspection authority and remain custody readers,
alongside owner and sudo administrators. This facility does not change group
membership or socket permissions. Human release review must approve that actual
reader set; delivery does not conceal container configuration from daemon admins.

The Compose configuration must be a private, single-link, nonsymlink regular
file owned by the runtime UID, beneath its canonical home, at most 1 MiB, with
the registered digest. Parent components must have safe ownership and modes.
Standard YAML is accepted with duplicate mappings, aliases, explicit tags,
merges, excessive depth and excessive nodes rejected. Top-level configuration
is limited to version, name, services, volumes and networks. Build, env_file,
extends, develop, configs and secrets entries are denied for every service.
The selected service image must equal the pinned digest. Its environment is
the approved slot map, each value literally `${SLOT}`, plus reviewed literal
nonsecret settings. No current slot
reference is allowed elsewhere. Other services may contain simple references
to different safe slots in their environment maps, but none receives this
consumer's values. Those unprovided slots resolve without ambient inheritance.
Null/list environment inheritance and interpolation outside these maps are
denied. Literal reviewed commands, healthchecks, volumes and other normal
service configuration remain part of the measured artifact.

The only launched argument vector is:

```text
/usr/bin/docker compose --env-file /dev/null --project-name PROJECT
  --project-directory COMPOSE_PARENT --file COMPOSE_FILE
  up --detach --no-deps --no-build --pull never SERVICE
```

The installed executable and existing system Compose plugin discovery paths
must have canonical, root-owned, nonwritable custody. HOME and DOCKER_CONFIG
point to a fixed nonexistent location, preventing user configuration/plugins.
The complete `ExplicitEnvironment` contains only fixed PATH from `os.defpath`,
C locale, that empty configuration location, exact Unix DOCKER_HOST and the
selected slots. Loader, interpreter, shell, Compose, Docker and Sandbox control
variables are forbidden slots. No parent environment is read or copied.
`BoundedProcessRunner(max_output=0)` discards child stdout and stderr. The
consumer clears its environment in `finally`; its only results are `succeeded`,
`failed` and `effects_unknown`, with no exception text, argv values, logs or
secret-bearing return structure.

Docker intentionally retains the selected container environment in daemon
configuration. This is the protected server runtime facility, with access
confined to existing daemon administrators, the full owner and sudo administrators.
Broker values must never
enter client/browser responses, ordinary polls, retained ledgers, source trees,
arbitrary credential files, environment artifacts or logs. Trusted image and
configuration review must establish that the intended application does not
print, persist, forward or expose its received values. Digest pinning does not
establish that behavior by itself. A privileged image, host/socket mount,
logging configuration or application endpoint can invalidate custody and must
be rejected by the registered artifact's human security review.

Database administrator and initializer credentials belong to separately mapped
database/initializer consumers, never Hub application slots. A reviewer must
check the actual source manifest, image/configuration and each recipient's
mapping. This module cannot infer an application's semantics from variable
names and makes no actual Hub artifact compatibility claim. No Hub repository
change, mandatory application protocol, install, engine call or provider action
is authorized by this source contract.

Remaining release evidence includes sibling owner endpoint integration,
registered resolver/manifest composition, source review, synthetic behavioral
checks, actual installation revision, protected daemon/socket custody, actual
image/configuration review, correct separation of database/admin recipients,
real provider and separately generated runtime consumption, and human approval.
