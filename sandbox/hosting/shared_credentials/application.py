"""Fixed protected server runtime delivery, never a generic process consumer.

The reviewed image intentionally receives its exact environment slots. Docker
retains container configuration, so daemon custody belongs to the full owner
and sudo administrators. This module does not advertise installed capability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import time

from sandbox.services.environment import ExplicitEnvironment
from sandbox.services.process import BoundedProcessRunner
from .consumers import ConsumerError
from .models import CredentialIntent


_DOCKER = Path("/usr/bin/docker")
_EMPTY_HOME = Path("/nonexistent/sandbox-runtime-env-v1")
_MAX_COMPOSE_BYTES = 1024 * 1024
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
_SLOT = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]{0,127})\}\Z")
_CONTROL_NAMES = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP",
    "ENV", "BASH_ENV", "SHELLOPTS", "PS4", "PROMPT_COMMAND", "NODE_OPTIONS",
    "PYTHONPATH", "PYTHONHOME", "PERL5OPT", "RUBYOPT", "GIT_ASKPASS", "SSH_ASKPASS",
})
_CONTROL_PREFIXES = ("DOCKER_", "COMPOSE_", "LD_", "DYLD_", "GIT_", "SANDBOX_")


def _slot(value):
    return (isinstance(value, str) and _SLOT.fullmatch(value) is not None
            and value not in _CONTROL_NAMES and not value.startswith(_CONTROL_PREFIXES))


def _path(value):
    return (isinstance(value, Path) and value.is_absolute()
            and not any(part in (".", "..") for part in value.parts)
            and all(ord(char) >= 32 and char != "$" for char in str(value)))


@dataclass(frozen=True, slots=True)
class RuntimeAction:
    """Nonsecret server-owned action record. Construction grants no authority."""
    compose_file: Path
    compose_digest: str
    compose_project: str
    service: str
    image: str
    engine_socket: Path
    slot_names: tuple[str, ...]
    owner_uid: int
    original_deadline: float

    def __post_init__(self):
        if (not _path(self.compose_file) or not _path(self.engine_socket)
                or not isinstance(self.compose_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", self.compose_digest) is None
                or not isinstance(self.compose_project, str)
                or _NAME.fullmatch(self.compose_project) is None
                or not isinstance(self.service, str) or _NAME.fullmatch(self.service) is None
                or not isinstance(self.image, str) or len(self.image) > 512
                or re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", self.image) is None
                or type(self.owner_uid) is not int or self.owner_uid < 0
                or type(self.slot_names) is not tuple or not 1 <= len(self.slot_names) <= 128
                or any(not _slot(name) for name in self.slot_names)
                or len(set(self.slot_names)) != len(self.slot_names)
                or type(self.original_deadline) not in (int, float)
                or not math.isfinite(self.original_deadline) or self.original_deadline <= 0):
            raise ConsumerError("credential_runtime_action_invalid")


def _trusted_root_path(path, *, executable=False, absent=False):
    """Check every existing component, including plugin discovery directories."""
    cursor = Path("/")
    for part in path.parts[1:]:
        cursor /= part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            if absent:
                return
            raise ConsumerError()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022):
            raise ConsumerError()
    if absent:
        raise ConsumerError()
    if executable and (not stat.S_ISREG(info.st_mode)
                       or not info.st_mode & 0o111 or info.st_mode & 0o6000):
        raise ConsumerError()


def _trusted_docker():
    _trusted_root_path(_DOCKER, executable=True)
    _trusted_root_path(_EMPTY_HOME, absent=True)
    # With an empty HOME/config, these are Docker's system plugin locations.
    # Existing directories and plugin files must share root-only write custody.
    for directory in ("/usr/local/lib/docker/cli-plugins", "/usr/local/libexec/docker/cli-plugins",
                      "/usr/lib/docker/cli-plugins", "/usr/libexec/docker/cli-plugins"):
        path = Path(directory)
        if path.exists():
            _trusted_root_path(path)
            plugin = path / "docker-compose"
            if plugin.exists() or plugin.is_symlink():
                _trusted_root_path(plugin, executable=True)
        else:
            _trusted_root_path(path, absent=True)


def _yaml(content):
    """Bounded standard Compose YAML without aliases, tags or duplicate keys."""
    import yaml

    class Loader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise ConsumerError()
            event = self.peek_event()
            if getattr(event, "anchor", None) is not None or getattr(event, "tag", None) is not None:
                raise ConsumerError()
            self._depth += 1
            self._nodes += 1
            if self._depth > 32 or self._nodes > 16384:
                raise ConsumerError()
            try:
                return super().compose_node(parent, index)
            finally:
                self._depth -= 1

        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result or key == "<<":
                    raise ConsumerError()
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    loader = Loader(content)
    loader._depth = loader._nodes = 0
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def _walk_literal(value):
    if isinstance(value, str):
        if "$" in value or "\x00" in value:
            raise ConsumerError()
    elif type(value) is dict:
        for key, item in value.items():
            _walk_literal(key)
            _walk_literal(item)
    elif type(value) is list:
        for item in value:
            _walk_literal(item)
    elif value is not None and type(value) not in (bool, int, float):
        raise ConsumerError()


def _compose(action, home):
    """Read configuration only; never a secret source or generated env file."""
    action.compose_file.relative_to(home)
    cursor = home
    for part in action.compose_file.relative_to(home).parts:
        cursor /= part
        info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != action.owner_uid or info.st_mode & 0o022:
            raise ConsumerError()
    fd = os.open(action.compose_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != action.owner_uid or before.st_mode & 0o077
                or not 0 < before.st_size <= _MAX_COMPOSE_BYTES):
            raise ConsumerError()
        content = stream.read(_MAX_COMPOSE_BYTES + 1)
        after = os.fstat(stream.fileno())
        observation = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if (observation(before) != observation(after) or len(content) > _MAX_COMPOSE_BYTES
                or hashlib.sha256(content).hexdigest() != action.compose_digest):
            raise ConsumerError()
    document = _yaml(content)
    if (type(document) is not dict or set(document) - {"version", "name", "services", "volumes", "networks"}
            or type(document.get("services")) is not dict or not document["services"]):
        raise ConsumerError()
    services = document.pop("services")
    _walk_literal(document)
    if action.service not in services:
        raise ConsumerError()
    selected = set(action.slot_names)
    for name, service in services.items():
        if (not isinstance(name, str) or _NAME.fullmatch(name) is None
                or type(service) is not dict
                or any(key in service for key in ("build", "env_file", "extends", "develop", "configs", "secrets"))):
            raise ConsumerError()
        environment = service.pop("environment", {})
        if type(environment) is not dict:
            raise ConsumerError()
        if name == action.service:
            if service.get("image") != action.image or not selected <= environment.keys():
                raise ConsumerError()
            if any(environment[slot] != "${" + slot + "}" for slot in selected):
                raise ConsumerError()
            for key, value in environment.items():
                if key not in selected:
                    _walk_literal(key)
                    if value is None:
                        raise ConsumerError()
                    _walk_literal(value)
        else:
            for destination, value in environment.items():
                _walk_literal(destination)
                if isinstance(value, str) and "$" in value:
                    reference = _REFERENCE.fullmatch(value)
                    if (reference is None or not _slot(reference[1])
                            or reference[1] in selected):
                        raise ConsumerError()
                else:
                    # Explicit literals are reviewed configuration. Null would
                    # request ambient inheritance and is therefore forbidden.
                    if value is None:
                        raise ConsumerError()
                    _walk_literal(value)
        # Literal reviewed command/healthcheck/volume configuration is allowed.
        # Every current secret reference must occur only in the exact mapping.
        _walk_literal(service)


def _owner(action):
    from sandbox.core._docker import _require_local_docker_runtime
    from sandbox.hosting.endpoints.local import owner_local, require_current

    _require_local_docker_runtime()
    owner = owner_local()
    if (owner.uid != action.owner_uid or owner.uid != os.geteuid()
            or Path(owner.policy.engine_socket) != action.engine_socket
            or owner.home != owner.home.resolve(strict=True)):
        raise ConsumerError()
    action.compose_file.relative_to(owner.home)
    # Existing Docker-group members are already daemon administrators with full
    # container inspection rights. Do not pretend delivery removes those rights
    # or require a production socket permission migration. World access refuses.
    socket = action.engine_socket.lstat()
    if (not stat.S_ISSOCK(socket.st_mode) or socket.st_uid not in (0, owner.uid)
            or socket.st_mode & 0o007 or action.engine_socket != action.engine_socket.resolve(strict=True)):
        raise ConsumerError()
    require_current(owner, engine=True)
    return owner


@dataclass(frozen=True, slots=True)
class RuntimeEnvironmentConsumer:
    intent_digest: str
    action: RuntimeAction = field(repr=False)
    consumer_id: str
    consumer_revision: str = field(default="runtime-env-v1", init=False)
    purpose: str = field(default="runtime-environment", init=False)

    def __post_init__(self):
        if (not isinstance(self.intent_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", self.intent_digest) is None
                or type(self.action) is not RuntimeAction or not isinstance(self.consumer_id, str)
                or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:@+-]{0,127}", self.consumer_id) is None):
            raise ConsumerError()

    def matches(self, intent: CredentialIntent) -> bool:
        if type(intent) is not CredentialIntent:
            return False
        mappings = tuple(item for item in intent.mappings if item.consumer_id == self.consumer_id)
        return (intent.intent_digest() == self.intent_digest
                and len(mappings) == len(self.action.slot_names)
                and {item.destination for item in mappings} == set(self.action.slot_names)
                and all(item.kind in ("provider", "generated")
                        and item.consumer_revision == self.consumer_revision
                        and item.purpose == self.purpose for item in mappings))

    def consume(self, slots):
        """Consume an explicit broker bag under the actual current held effect."""
        environment = ExplicitEnvironment()
        started = False
        outcome = "failed"
        try:
            from .service import require_current_consumer_effect
            checkpoint = require_current_consumer_effect(
                intent_digest=self.intent_digest, consumer_id=self.consumer_id)
            checkpoint.require_current()
            action = self.action
            if (type(slots) is not dict or set(slots) != set(action.slot_names)
                    or any(not isinstance(value, str) or not value or len(value) > 65536
                           or "\x00" in value for value in slots.values())):
                return "failed"
            owner = _owner(action)
            _trusted_docker()
            _compose(action, owner.home)
            environment.update({"PATH": os.defpath, "LANG": "C", "LC_ALL": "C",
                                "HOME": str(_EMPTY_HOME), "DOCKER_CONFIG": str(_EMPTY_HOME),
                                "DOCKER_HOST": "unix://" + str(action.engine_socket)})
            environment.update(slots)
            argv = (str(_DOCKER), "compose", "--env-file", "/dev/null", "--project-name",
                    action.compose_project, "--project-directory", str(action.compose_file.parent),
                    "--file", str(action.compose_file), "up", "--detach", "--no-deps",
                    "--no-build", "--pull", "never", action.service)
            # Revalidate after all blocking inspection. Neither this record nor
            # a supplied callback can create/recover the concrete checkpoint.
            from sandbox.hosting.endpoints.local import require_current
            require_current(owner, engine=True)
            current = require_current_consumer_effect(
                intent_digest=self.intent_digest, consumer_id=self.consumer_id)
            if current is not checkpoint:
                raise ConsumerError()
            checkpoint.require_current()
            deadline = min(action.original_deadline, checkpoint.effective_deadline)
            remaining = deadline - time.time()
            if remaining <= 0:
                return "failed"
            started = True
            result = BoundedProcessRunner(max_output=0).run(
                argv, cwd=str(action.compose_file.parent), env=environment, timeout=remaining)
            outcome = "succeeded" if result.returncode == 0 and result.termination_reason is None else "effects_unknown"
            current = require_current_consumer_effect(
                intent_digest=self.intent_digest, consumer_id=self.consumer_id)
            if current is not checkpoint:
                raise ConsumerError()
            checkpoint.require_current()
            if time.time() >= min(action.original_deadline, checkpoint.effective_deadline):
                outcome = "effects_unknown"
        except BaseException:
            # No parser/engine/authority exception, traceback or child output
            # crosses this boundary. A started effect is never safe to replay.
            outcome = "effects_unknown" if started else "failed"
        finally:
            environment.clear()
        return outcome


__all__ = ["RuntimeAction", "RuntimeEnvironmentConsumer"]
