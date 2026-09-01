"""Fixed no-build/no-pull remote runtime adapter for Feature 051.

The adapter accepts no registry authentication, credential, pull, build, tag,
or prune operation.  All calls use a closed synthetic environment and bounded
output/deadline contracts supplied by the registered-host runner.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DIGEST_REF = re.compile(r"[a-z0-9.]+/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}\Z")
CLOSED_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
MAX_REMOTE_OUTPUT = 1024 * 1024


class RemoteActivationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InitHandle:
    identity: str
    name: str
    expected: dict


class RegisteredRemoteActivationTransport:
    def __init__(self, *, argv_runner: Callable, topology_renderer: Callable | None = None,
                 target_observer: Callable | None = None,
                 target_identity_observer: Callable | None = None,
                 init_environment_provider: Callable | None = None) -> None:
        self._run = argv_runner
        self._render = topology_renderer or self._render_default
        self._observe = target_observer or self._observe_default
        self._target_identity = target_identity_observer
        self._init_environment_provider = init_environment_provider
        self._init_environment_sources: dict[str, dict] = {}
        self._compose_selector: dict[str, object] = {}

    @staticmethod
    def _service(value: str) -> str:
        if type(value) is not str or _ID.fullmatch(value) is None:
            raise RemoteActivationError("topology_mismatch")
        return value

    @staticmethod
    def _image(value: str) -> str:
        if type(value) is not str or _DIGEST_REF.fullmatch(value) is None:
            raise RemoteActivationError("local_image_mismatch")
        return value

    def _invoke(self, argv: tuple[str, ...], *, timeout_seconds: int,
                max_output_bytes: int = MAX_REMOTE_OUTPUT,
                environment: dict[str, str] | None = None,
                private_environment: dict[str, str] | None = None,
                private_environment_source: dict | None = None,
                redact_environment_keys: tuple[str, ...] | None = None) -> dict:
        if type(timeout_seconds) is not int or timeout_seconds < 1 or timeout_seconds > 3600:
            raise RemoteActivationError("effect_unknown")
        result = self._run(argv=argv, environment={**CLOSED_ENVIRONMENT, **(environment or {})},
                           private_environment=dict(private_environment or {}),
                           private_environment_source=dict(private_environment_source or {}),
                           redact_environment_keys=(None if redact_environment_keys is None
                                                    else tuple(redact_environment_keys)),
                           timeout_seconds=timeout_seconds,
                           max_output_bytes=max_output_bytes)
        if type(result) is not dict or set(result) != {"returncode", "stdout", "stderr", "terminated"} \
                or type(result["stdout"]) is not str or type(result["stderr"]) is not str \
                or len(result["stdout"].encode()) + len(result["stderr"].encode()) > max_output_bytes:
            raise RemoteActivationError("effect_unknown")
        return result

    def render_topology(self, **selectors) -> dict:
        return self._render(**selectors)

    def _observed_target(self, runtime_epoch: str) -> dict:
        if not callable(self._target_identity):
            raise RemoteActivationError("runtime_mismatch")
        identity = self._target_identity()
        if type(identity) is not dict or set(identity) != {"machine_identity", "target_identity"} \
                or any(type(identity[name]) is not str or not identity[name]
                       for name in identity):
            raise RemoteActivationError("runtime_mismatch")
        return {**identity, "daemon_identity": runtime_epoch}

    def _render_default(self, *, compose_files, project_name, selected_services,
                        image_overrides) -> dict:
        self._compose_selector = {}
        argv = ["docker", "compose"]
        for path in compose_files: argv.extend(("--file", path))
        argv.extend(("--project-name", project_name, "config", "--format", "json"))
        environment = {
            f"SANDBOX_ACTIVATION_IMAGE_{name.upper().replace('-', '_')}": image
            for name, image in image_overrides.items()}
        result = self._invoke(tuple(argv), timeout_seconds=60, environment=environment)
        try: rendered = json.loads(result["stdout"])
        except (TypeError, json.JSONDecodeError): raise RemoteActivationError("topology_mismatch") from None
        services = rendered.get("services") if isinstance(rendered, dict) else None
        if result["returncode"] != 0 or not isinstance(services, dict):
            raise RemoteActivationError("topology_mismatch")
        render_digest = "sha256:" + hashlib.sha256(json.dumps(
            rendered, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        normalized = {}
        for name in selected_services:
            value = services.get(name)
            if not isinstance(value, dict): raise RemoteActivationError("topology_mismatch")
            platform_text = value.get("platform")
            if not isinstance(platform_text, str): raise RemoteActivationError("topology_mismatch")
            pieces = platform_text.split("/")
            platform = {"os": pieces[0], "architecture": pieces[1]}
            if len(pieces) == 3: platform["variant"] = pieces[2]
            depends = value.get("depends_on") or {}
            normalized[name] = {"image": value.get("image"), "build": value.get("build"),
                "pull_policy": value.get("pull_policy"), "platform": platform,
                "dependencies": sorted(depends),
                "topology_identity": value.get("labels", {}).get(
                    "org.sandbox.application-topology.v1"),
                "configuration_digest": "sha256:" + hashlib.sha256(json.dumps(
                    value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        epoch = self._observe_default(kind="epoch", target={})
        self._compose_selector = {"compose_files": tuple(compose_files),
            "project_name": project_name, "environment": dict(environment),
            "render_digest": render_digest}
        return {"services": normalized, "orphans": sorted(set(services) - set(selected_services)),
                "runtime_epoch": epoch["runtime_epoch"]}

    def _observe_default(self, *, kind, **selectors):
        if kind == "epoch":
            result = self._invoke(("docker", "info", "--format", "{{.ID}}"), timeout_seconds=30)
            identity = result["stdout"].strip()
            if result["returncode"] != 0 or not identity:
                raise RemoteActivationError("runtime_mismatch")
            image = selectors.get("image")
            local_id = None
            platform = None
            if image:
                inspected = self._invoke(("docker", "image", "inspect", image),
                                         timeout_seconds=30)
                try: image_raw = json.loads(inspected["stdout"])[0]
                except (TypeError, IndexError, json.JSONDecodeError):
                    raise RemoteActivationError("local_image_mismatch") from None
                if inspected["returncode"] != 0 or not image_raw.get("Id"):
                    raise RemoteActivationError("local_image_mismatch")
                local_id = image_raw["Id"]
                platform = {"os": image_raw.get("Os"),
                            "architecture": image_raw.get("Architecture")}
                if image_raw.get("Variant"): platform["variant"] = image_raw["Variant"]
            return {"runtime_epoch": identity, "local_image_id": local_id,
                    "platform": platform}
        if kind == "init_inspection":
            raw = selectors.get("raw") or {}; expected = selectors.get("expected") or {}
            if isinstance(raw, list) and len(raw) == 1: raw = raw[0]
            config = raw.get("Config") or {}; state = raw.get("State") or {}
            host = raw.get("HostConfig") or {}; network = raw.get("NetworkSettings") or {}
            if state.get("Running") is True:
                raise RemoteActivationError("init_mismatch")
            declared_env = expected.get("environment_keys") or []
            if config.get("Env") is not None or raw.get("DeclaredEnvironmentMatch") is not True \
                    or raw.get("DeclaredEnvironmentKeys") != sorted(declared_env):
                raise RemoteActivationError("init_mismatch")
            # Image-default variables are neither authority nor a mismatch.
            env_keys = sorted(declared_env)
            mounts = []
            for item in raw.get("Mounts") or []:
                if not isinstance(item, dict): raise RemoteActivationError("init_mismatch")
                source = item.get("Name") if item.get("Type") == "volume" else item.get("Source")
                fields = [f"type={item.get('Type')}", f"source={source}",
                          f"target={item.get('Destination')}"]
                if item.get("RW") is False: fields.append("readonly")
                mounts.append(",".join(fields))
            labels = config.get("Labels") or {}
            try: dependencies = json.loads(labels.get("org.sandbox.activation.dependencies", "[]"))
            except json.JSONDecodeError: raise RemoteActivationError("init_mismatch") from None
            epoch = self._observe_default(kind="epoch", target={}, image=expected.get("image"))
            observed_target = self._observed_target(epoch["runtime_epoch"])
            platform_value = epoch.get("platform")
            if type(platform_value) is not dict:
                raise RemoteActivationError("init_mismatch")
            actual = {"image": config.get("Image"), "local_image_id": raw.get("Image"),
                "platform": platform_value,
                "command": config.get("Cmd") or [], "mounts": sorted(mounts),
                "networks": sorted((network.get("Networks") or {}).keys()),
                "environment_keys": env_keys, "privileged": host.get("Privileged") is True,
                "dependencies": dependencies, "target": observed_target,
                "runtime_epoch": epoch.get("runtime_epoch")}
            return actual
        if kind == "local":
            image = selectors["repository_digest"]
            start_epoch = self._observe_default(kind="epoch", target={})["runtime_epoch"]
            start_target = self._observed_target(start_epoch)
            result = self._invoke(("docker", "image", "inspect", image), timeout_seconds=30)
            try: raw = json.loads(result["stdout"])[0]
            except (TypeError, IndexError, json.JSONDecodeError):
                raise RemoteActivationError("local_image_mismatch") from None
            image_id = raw.get("Id"); repo_digests = raw.get("RepoDigests") or []
            end_epoch = self._observe_default(kind="epoch", target={})["runtime_epoch"]
            end_target = self._observed_target(end_epoch)
            return {"repository": image.split("@", 1)[0].split("/", 1)[1],
                    "repo_digest": image, "config_digest": image_id,
                    "platform": {"os": raw.get("Os"), "architecture": raw.get("Architecture")},
                    "local_image_id": image_id,
                    "target_epoch_start": start_target["machine_identity"],
                    "target_epoch_end": end_target["machine_identity"],
                    "daemon_epoch_start": start_epoch,
                    "daemon_epoch_end": end_epoch,
                    "repo_digests": repo_digests}
        if kind == "running":
            services = selectors["services"]
            start_epoch = self._observe_default(kind="epoch", target={})["runtime_epoch"]
            start_target = self._observed_target(start_epoch)
            result = self._invoke(("docker", "ps", "--format", "{{json .}}"), timeout_seconds=60)
            rows = []
            for line in result["stdout"].splitlines():
                try: item = json.loads(line)
                except json.JSONDecodeError: continue
                service = (item.get("Labels") or "")
                label = next((part.split("=", 1)[1] for part in service.split(",")
                              if part.startswith("com.docker.compose.service=")), None)
                if label in services:
                    inspected = self._invoke(("docker", "inspect", item["ID"]), timeout_seconds=30)
                    try: raw = json.loads(inspected["stdout"])[0]
                    except (TypeError, IndexError, json.JSONDecodeError): continue
                    config = raw.get("Config") or {}; image_id = raw.get("Image")
                    labels = config.get("Labels") or {}
                    image_inspect = self._invoke(("docker", "image", "inspect", image_id),
                                                 timeout_seconds=30)
                    try: image_raw = json.loads(image_inspect["stdout"])[0]
                    except (TypeError, IndexError, json.JSONDecodeError):
                        raise RemoteActivationError("runtime_mismatch") from None
                    if image_inspect["returncode"] != 0 or image_raw.get("Id") != image_id:
                        raise RemoteActivationError("runtime_mismatch")
                    platform = {"os": image_raw.get("Os"),
                                "architecture": image_raw.get("Architecture")}
                    if image_raw.get("Variant"): platform["variant"] = image_raw["Variant"]
                    rows.append({"service": label, "declared_image": config.get("Image"),
                                 "repository_digest": config.get("Image"),
                                 "local_image_id": image_id, "config_digest": image_id,
                                 "platform": platform,
                                 "topology_identity": labels.get(
                                     "org.sandbox.application-topology.v1"),
                                 "healthy": (raw.get("State") or {}).get("Health", {}).get("Status") == "healthy"})
            end_epoch = self._observe_default(kind="epoch", target={})["runtime_epoch"]
            end_target = self._observed_target(end_epoch)
            return {"target_epoch_start": start_target["machine_identity"],
                    "target_epoch_end": end_target["machine_identity"],
                    "runtime_epoch_start": start_epoch, "runtime_epoch_end": end_epoch,
                    "services": rows}
        raise RemoteActivationError("runtime_mismatch")

    def create_init(self, *, declaration: dict, image: str, platform: dict,
                    target: dict, start: bool) -> InitHandle:
        if start is not False:
            raise RemoteActivationError("init_mismatch")
        image = self._image(image)
        name = "sandbox-activation-init-" + declaration["configuration_digest"].split(":", 1)[1][:24]
        argv = ["docker", "create", "--name", name, "--pull=never"]
        if declaration["privileged"]:
            raise RemoteActivationError("init_mismatch")
        platform_text = "/".join(filter(None, (platform.get("os"), platform.get("architecture"),
                                                platform.get("variant"))))
        argv.extend(("--platform", platform_text, "--label",
                     "org.sandbox.activation.dependencies=" + json.dumps(
                         declaration["dependencies"], separators=(",", ":"))))
        for network in declaration["networks"]: argv.extend(("--network", network))
        for mount in declaration["mounts"]: argv.extend(("--mount", mount))
        source = {**self._compose_selector, "service": declaration["service"],
                  "keys": tuple(declaration["environment_keys"])}
        provider_used = callable(self._init_environment_provider)
        if provider_used:
            environment_values = self._init_environment_provider(
                declaration["service"], tuple(declaration["environment_keys"]))
        else:
            environment_values = {}
        if (provider_used and (type(environment_values) is not dict
                or set(environment_values) != set(declaration["environment_keys"]) \
                or any(type(key) is not str or _ID.fullmatch(key) is None or
                       type(value) is not str or len(value.encode()) > 65536
                       for key, value in environment_values.items()))):
            raise RemoteActivationError("init_mismatch")
        if not provider_used and source["keys"] and not self._compose_selector:
            raise RemoteActivationError("init_mismatch")
        for key in declaration["environment_keys"]: argv.extend(("--env", key))
        argv.extend((image, *declaration["command"]))
        result = self._invoke(tuple(argv), timeout_seconds=30,
                              private_environment=environment_values,
                              private_environment_source={} if provider_used else source)
        if result["returncode"] != 0 or result["terminated"] is not True:
            raise RemoteActivationError("init_mismatch")
        identity = result["stdout"].strip()
        if not identity or len(identity) > 128:
            raise RemoteActivationError("init_mismatch")
        self._init_environment_sources[identity] = {
            "values": dict(environment_values), "source": {} if provider_used else source}
        try:
            epoch = self._observe(kind="epoch", target=target, image=image)
            if type(epoch) is not dict or type(epoch.get("runtime_epoch")) is not str:
                raise RemoteActivationError("init_mismatch")
        except Exception:
            cleaned = False
            try: cleaned = self.remove_init(InitHandle(identity, name, {}), force=False)
            finally: self._init_environment_sources.pop(identity, None)
            if cleaned is not True:
                raise RemoteActivationError("effect_unknown") from None
            raise
        expected = {"image": image, "local_image_id": epoch.get("local_image_id"),
                    "platform": platform, "command": declaration["command"],
                    "mounts": sorted(declaration["mounts"]),
                    "networks": sorted(declaration["networks"]),
                    "environment_keys": sorted(declaration["environment_keys"]),
                    "privileged": declaration["privileged"],
                    "dependencies": declaration["dependencies"], "target": target,
                    "runtime_epoch": epoch["runtime_epoch"]}
        return InitHandle(identity, name, expected)

    def inspect_init(self, handle: InitHandle) -> dict:
        private = self._init_environment_sources.get(handle.identity) or {}
        result = self._invoke(("docker", "inspect", "--format", "{{json .}}", handle.identity),
                              timeout_seconds=30,
                              private_environment=private.get("values", {}),
                              private_environment_source=private.get("source", {}),
                              redact_environment_keys=tuple(handle.expected["environment_keys"]))
        if result["returncode"] != 0 or result["terminated"] is not True:
            raise RemoteActivationError("init_mismatch")
        try: value = json.loads(result["stdout"])
        except json.JSONDecodeError: raise RemoteActivationError("init_mismatch") from None
        normalized = self._observe(kind="init_inspection", raw=value,
                                   expected=handle.expected)
        if type(normalized) is not dict:
            raise RemoteActivationError("init_mismatch")
        return normalized

    def start_init(self, handle: InitHandle) -> None:
        result = self._invoke(("docker", "start", handle.identity), timeout_seconds=30)
        if result["returncode"] != 0:
            raise RemoteActivationError("effect_unknown")

    def wait_init(self, handle: InitHandle, *, timeout_seconds: int,
                  max_output_bytes: int) -> dict:
        result = self._invoke(("docker", "wait", handle.identity),
                              timeout_seconds=timeout_seconds,
                              max_output_bytes=max_output_bytes)
        try: exit_code = int(result["stdout"].strip())
        except ValueError: raise RemoteActivationError("effect_unknown") from None
        return {"exit_code": exit_code, "terminated": result["terminated"],
                "output_bytes": len(result["stdout"].encode()) + len(result["stderr"].encode()),
                "cancelled": False}

    def cancel_init(self, handle: InitHandle) -> bool:
        result = self._invoke(("docker", "kill", handle.identity), timeout_seconds=30)
        return result["terminated"] is True and result["returncode"] in {0, 1}

    def wait_terminated(self, handle: InitHandle, *, timeout_seconds: int) -> bool:
        result = self._invoke(("docker", "inspect", "--format", "{{.State.Running}}", handle.identity),
                              timeout_seconds=timeout_seconds)
        return result["returncode"] == 0 and result["stdout"].strip() == "false"

    def remove_init(self, handle: InitHandle, *, force: bool) -> bool:
        argv = ["docker", "rm"]
        if force: argv.append("--force")
        argv.append(handle.identity)
        result = self._invoke(tuple(argv), timeout_seconds=30)
        removed = result["returncode"] == 0 and result["terminated"] is True
        if removed: self._init_environment_sources.pop(handle.identity, None)
        return removed

    def replace_services(self, *, compose_files: tuple[str, ...], project_name: str,
                         services: tuple[str, ...], exact_image: str,
                         environment_overrides: dict[str, str], timeout_seconds: int) -> None:
        self._image(exact_image)
        if any(key not in {f"SANDBOX_ACTIVATION_IMAGE_{name.upper().replace('-', '_')}"
                           for name in services} for key in environment_overrides):
            raise RemoteActivationError("topology_mismatch")
        if any(value != exact_image for value in environment_overrides.values()):
            raise RemoteActivationError("topology_mismatch")
        argv = ["docker", "compose"]
        for path in compose_files: argv.extend(("--file", path))
        argv.extend(("--project-name", project_name, "up", "--detach", "--no-build",
                     "--pull", "never", "--no-deps", *map(self._service, services)))
        result = self._run(argv=tuple(argv), environment={**CLOSED_ENVIRONMENT,
                           **environment_overrides}, timeout_seconds=timeout_seconds,
                           max_output_bytes=MAX_REMOTE_OUTPUT)
        if type(result) is not dict or result.get("returncode") != 0 \
                or result.get("terminated") is not True:
            raise RemoteActivationError("effect_unknown")

    def observe_local_image(self, **selectors) -> dict:
        return self._observe(kind="local", **selectors)

    def observe_running(self, **selectors) -> dict:
        return self._observe(kind="running", **selectors)
