"""Fixed no-build/no-pull remote runtime adapter for Feature 051.

The adapter accepts no registry authentication, credential, pull, build, tag,
or prune operation.  All calls use a closed synthetic environment and bounded
output/deadline contracts supplied by the registered-host runner.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Callable


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DIGEST_REF = re.compile(r"[a-z0-9.]+/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}\Z")
CLOSED_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
MAX_REMOTE_OUTPUT = 1024 * 1024
_CONFIGURATION_HMAC_KEY = "SANDBOX_ACTIVATION_CONFIGURATION_HMAC_KEY"


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
                 init_environment_provider: Callable | None = None,
                 configuration_binding_key: bytes | None = None) -> None:
        if configuration_binding_key is not None \
                and (type(configuration_binding_key) is not bytes
                     or len(configuration_binding_key) != 32):
            raise RemoteActivationError("topology_mismatch")
        self._run = argv_runner
        self._render = topology_renderer or self._render_default
        self._observe = target_observer or self._observe_default
        self._target_identity = target_identity_observer
        self._init_environment_provider = init_environment_provider
        self._configuration_binding_key = configuration_binding_key
        self._init_environment_sources: dict[str, dict] = {}
        self._compose_selector: dict[str, object] = {}
        self._compose_selector_v2: dict[str, object] = {}

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
        source = dict(private_environment_source or {})
        private = dict(private_environment or {})
        needs_configuration_binding = bool(source) or argv[:1] == (
            "sandbox-activation-observe-running",) or (
            len(argv) >= 2 and argv[:2] == ("docker", "compose") and "config" in argv)
        if needs_configuration_binding:
            if self._configuration_binding_key is None or _CONFIGURATION_HMAC_KEY in private:
                raise RemoteActivationError("topology_mismatch")
            private[_CONFIGURATION_HMAC_KEY] = base64.b64encode(
                self._configuration_binding_key).decode()
        result = self._run(argv=argv, environment={**CLOSED_ENVIRONMENT, **(environment or {})},
                           private_environment=private,
                           private_environment_source=source,
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

    def render_topology_v2(self, *, compose_files: tuple[str, ...], project_name: str,
                           selected_services: tuple[str, ...],
                           service_image_bindings: dict[str, str],
                           environment_bindings: dict[str, str],
                           topology_digest: str, private_compose_snapshot: dict) -> dict:
        """Render through an opaque machine-local snapshot provider.

        The snapshot descriptor contains no Compose values.  The registered
        runner resolves it privately and returns only bounded HMAC identities.
        """
        self._compose_selector_v2 = {}
        if (type(private_compose_snapshot) is not dict
                or set(private_compose_snapshot) != {
                    "schema_version", "snapshot_id", "provider_revision", "target",
                    "plan_set_digest", "selected_services", "configuration_digest",
                    "expires_at", "snapshot_digest"}
                or private_compose_snapshot.get("schema_version") != 2
                or tuple(private_compose_snapshot.get("selected_services", ())) != selected_services
                or set(service_image_bindings) != set(selected_services)
                or set(environment_bindings.values()) != set(service_image_bindings.values())
                or re.fullmatch(r"sha256:[0-9a-f]{64}", topology_digest or "") is None):
            raise RemoteActivationError("topology_mismatch")
        images = {self._service(name): self._image(image)
                  for name, image in service_image_bindings.items()}
        environment = {}
        for variable, image in environment_bindings.items():
            if type(variable) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", variable) is None \
                    or image not in images.values():
                raise RemoteActivationError("topology_mismatch")
            environment[variable] = image
        argv = ["docker", "compose"]
        for path in compose_files:
            argv.extend(("--file", path))
        project_directory = os.path.dirname(os.path.abspath(compose_files[0]))
        argv.extend(("--project-directory", project_directory,
                     "--project-name", self._service(project_name),
                     "config", "--format", "json"))
        source = {"kind": "compose_snapshot_v2",
                  "snapshot_id": private_compose_snapshot["snapshot_id"],
                  "snapshot_digest": private_compose_snapshot["snapshot_digest"],
                  "provider_revision": private_compose_snapshot["provider_revision"],
                  "target": private_compose_snapshot["target"]}
        result = self._invoke(tuple(argv), timeout_seconds=60, environment=environment,
                              private_environment_source=source)
        try:
            rendered = json.loads(result["stdout"])
        except (TypeError, json.JSONDecodeError):
            raise RemoteActivationError("topology_mismatch") from None
        services = rendered.get("services") if isinstance(rendered, dict) else None
        if result["returncode"] != 0 or result["terminated"] is not True \
                or not isinstance(services, dict):
            raise RemoteActivationError("topology_mismatch")
        render_digest = rendered.pop("x-sandbox-configuration-digest", None)
        hashes = rendered.pop("x-sandbox-compose-config-hashes", None)
        markers = tuple(rendered.pop(name, None) for name in (
            "x-sandbox-has-configs", "x-sandbox-has-secrets",
            "x-sandbox-has-external-networks"))
        if (render_digest != private_compose_snapshot["configuration_digest"]
                or type(hashes) is not dict or set(hashes) != set(services)
                or any(re.fullmatch(r"sha256:[0-9a-f]{64}", value or "") is None
                       for value in hashes.values())
                or markers[0] is not False or markers[2] is not False
                or type(markers[1]) is not bool or set(rendered) != {"services"}
                or set(services) != set(selected_services)):
            raise RemoteActivationError("topology_mismatch")
        normalized = {}
        for name in selected_services:
            value = services.get(name)
            if not isinstance(value, dict) or value.get("image") != images[name] \
                    or value.get("build") is not None \
                    or value.get("pull_policy") not in {None, "never"} \
                    or value.get("platform") not in {None, "linux/amd64"}:
                raise RemoteActivationError("topology_mismatch")
            dependencies = value.get("depends_on") or {}
            normalized[name] = {"image": images[name], "build": None,
                "pull_policy": "never", "platform": {"os": "linux", "architecture": "amd64"},
                "dependencies": sorted(dependencies), "topology_identity": topology_digest,
                "compose_config_hash": hashes[name],
                "configuration_digest": "sha256:" + hashlib.sha256(json.dumps(
                    value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        epoch = self._observe_default(kind="epoch", target={})
        self._compose_selector_v2 = {
            "compose_files": tuple(compose_files), "project_name": project_name,
            "project_directory": project_directory, "environment": environment,
            "service_images": images, "selected_services": selected_services,
            "render_digest": render_digest, "runtime_epoch": epoch["runtime_epoch"],
            "compose_config_hashes": {name: hashes[name] for name in selected_services},
            "topology_digest": topology_digest, "private_source": source,
            "snapshot_digest": private_compose_snapshot["snapshot_digest"]}
        return {"services": normalized, "orphans": [],
                "runtime_epoch": epoch["runtime_epoch"],
                "configuration_digest": render_digest}

    def prepare_compose_snapshot_v2(self, *, compose_files: tuple[str, ...],
            project_name: str, selected_services: tuple[str, ...],
            service_image_bindings: dict[str, str],
            environment_bindings: dict[str, str], target: dict[str, str],
            snapshot_id: str, provider_revision: str) -> str:
        """Identify one registered private render through the target HMAC path."""
        if (type(target) is not dict or set(target) != {
                "machine_identity", "target_identity", "daemon_identity"}
                or type(snapshot_id) is not str or not snapshot_id.startswith("compose-snapshot/")
                or type(provider_revision) is not str or not provider_revision):
            raise RemoteActivationError("topology_mismatch")
        images = {self._service(name): self._image(image)
                  for name, image in service_image_bindings.items()}
        if set(images) != set(selected_services):
            raise RemoteActivationError("topology_mismatch")
        environment = {}
        for variable, image in environment_bindings.items():
            if type(variable) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", variable) is None \
                    or image not in images.values():
                raise RemoteActivationError("topology_mismatch")
            environment[variable] = image
        argv = ["docker", "compose"]
        for path in compose_files: argv.extend(("--file", path))
        directory = os.path.dirname(os.path.abspath(compose_files[0]))
        argv.extend(("--project-directory", directory, "--project-name",
                     self._service(project_name), "config", "--format", "json"))
        source = {"kind": "compose_prepare_v2", "snapshot_id": snapshot_id,
                  "provider_revision": provider_revision, "target": target}
        result = self._invoke(tuple(argv), timeout_seconds=60, environment=environment,
                              private_environment_source=source)
        try: rendered = json.loads(result["stdout"])
        except (TypeError, json.JSONDecodeError):
            raise RemoteActivationError("topology_mismatch") from None
        services = rendered.get("services") if isinstance(rendered, dict) else None
        digest = rendered.get("x-sandbox-configuration-digest") \
            if isinstance(rendered, dict) else None
        if (result["returncode"] != 0 or result["terminated"] is not True
                or type(services) is not dict or set(services) != set(selected_services)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", digest or "") is None):
            raise RemoteActivationError("topology_mismatch")
        for name in selected_services:
            row = services.get(name)
            if type(row) is not dict or row.get("image") != images[name] \
                    or row.get("build") is not None \
                    or row.get("pull_policy") not in {None, "never"} \
                    or row.get("platform") not in {None, "linux/amd64"}:
                raise RemoteActivationError("topology_mismatch")
        return digest

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
        project_directory = os.path.dirname(os.path.abspath(compose_files[0]))
        argv.extend(("--project-directory", project_directory,
                     "--project-name", project_name, "config", "--format", "json"))
        environment = {
            f"SANDBOX_ACTIVATION_IMAGE_{name.upper().replace('-', '_')}": image
            for name, image in image_overrides.items()}
        result = self._invoke(tuple(argv), timeout_seconds=60, environment=environment)
        try: rendered = json.loads(result["stdout"])
        except (TypeError, json.JSONDecodeError): raise RemoteActivationError("topology_mismatch") from None
        services = rendered.get("services") if isinstance(rendered, dict) else None
        if result["returncode"] != 0 or not isinstance(services, dict):
            raise RemoteActivationError("topology_mismatch")
        render_digest = rendered.pop("x-sandbox-configuration-digest", None)
        if type(render_digest) is not str \
                or re.fullmatch(r"sha256:[0-9a-f]{64}", render_digest) is None:
            raise RemoteActivationError("topology_mismatch")
        compose_config_hashes = rendered.pop("x-sandbox-compose-config-hashes", None)
        if type(compose_config_hashes) is not dict \
                or set(compose_config_hashes) != set(services) \
                or any(type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
                       for value in compose_config_hashes.values()):
            raise RemoteActivationError("topology_mismatch")
        has_configs = rendered.pop("x-sandbox-has-configs", None)
        has_secrets = rendered.pop("x-sandbox-has-secrets", None)
        has_external_networks = rendered.pop("x-sandbox-has-external-networks", None)
        if has_configs is not False or has_secrets is not False \
                or has_external_networks is not False or set(rendered) != {"services"}:
            raise RemoteActivationError("topology_mismatch")
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
                "compose_config_hash": compose_config_hashes[name],
                "configuration_digest": "sha256:" + hashlib.sha256(json.dumps(
                    value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        epoch = self._observe_default(kind="epoch", target={})
        self._compose_selector = {"compose_files": tuple(compose_files),
            "project_name": project_name,
            "project_directory": project_directory,
            "environment": dict(environment),
            "render_digest": render_digest,
            "runtime_epoch": epoch["runtime_epoch"]}
        return {"services": normalized, "orphans": sorted(set(services) - set(selected_services)),
                "runtime_epoch": epoch["runtime_epoch"],
                "configuration_digest": render_digest}

    def _observe_default(self, *, kind, **selectors):
        observation_type = kind
        if observation_type == "epoch":
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
        if observation_type == "init_inspection":
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
        if observation_type == "local":
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
            platform = {"os": raw.get("Os"), "architecture": raw.get("Architecture")}
            if raw.get("Variant"): platform["variant"] = raw["Variant"]
            return {"repository": image.split("@", 1)[0].split("/", 1)[1],
                    "repo_digest": image, "config_digest": image_id,
                    "platform": platform,
                    "local_image_id": image_id,
                    "target_epoch_start": start_target["machine_identity"],
                    "target_epoch_end": end_target["machine_identity"],
                    "target_identity_start": start_target["target_identity"],
                    "target_identity_end": end_target["target_identity"],
                    "daemon_epoch_start": start_epoch,
                    "daemon_epoch_end": end_epoch,
                    "repo_digests": repo_digests}
        if observation_type == "running":
            services = tuple(self._service(value) for value in selectors["services"])
            compose_project = self._service(selectors["compose_project"])
            start_epoch = self._observe_default(kind="epoch", target={})["runtime_epoch"]
            start_target = self._observed_target(start_epoch)
            result = self._invoke(("sandbox-activation-observe-running", compose_project,
                                   *services), timeout_seconds=60)
            try: rows = json.loads(result["stdout"])
            except (TypeError, json.JSONDecodeError):
                raise RemoteActivationError("runtime_mismatch") from None
            if result["returncode"] != 0 or type(rows) is not list:
                raise RemoteActivationError("runtime_mismatch")
            end_epoch = self._observe_default(kind="epoch", target={})["runtime_epoch"]
            end_target = self._observed_target(end_epoch)
            return {"target_epoch_start": start_target["machine_identity"],
                    "target_epoch_end": end_target["machine_identity"],
                    "target_identity_start": start_target["target_identity"],
                    "target_identity_end": end_target["target_identity"],
                    "runtime_epoch_start": start_epoch, "runtime_epoch_end": end_epoch,
                    "services": rows}
        raise RemoteActivationError("runtime_mismatch")

    def create_init(self, *, declaration: dict, image: str, platform: dict,
                    target: dict, start: bool) -> InitHandle:
        if start is not False:
            raise RemoteActivationError("init_mismatch")
        image = self._image(image)
        owner_body = {"target": target, "image": image,
                      "declaration_digest": declaration["configuration_digest"]}
        owner_digest = "sha256:" + hashlib.sha256(json.dumps(
            owner_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        name = "sandbox-activation-init-" + owner_digest.split(":", 1)[1][:24]
        argv = ["docker", "create", "--name", name, "--pull=never"]
        if declaration["privileged"]:
            raise RemoteActivationError("init_mismatch")
        platform_text = "/".join(filter(None, (platform.get("os"), platform.get("architecture"),
                                                platform.get("variant"))))
        argv.extend(("--platform", platform_text, "--label",
                     "org.sandbox.activation.init-owner=" + owner_digest,
                     "--label",
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
        try:
            result = self._invoke(tuple(argv), timeout_seconds=30,
                                  private_environment=environment_values,
                                  private_environment_source={} if provider_used else source)
            identity = result["stdout"].strip()
            if result["returncode"] != 0 or result["terminated"] is not True \
                    or not identity or len(identity) > 128:
                raise RemoteActivationError("init_mismatch")
        except Exception:
            if not self._remove_or_prove_absent(name, owner_digest):
                raise RemoteActivationError("effect_unknown") from None
            raise
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

    def _remove_or_prove_absent(self, name: str, owner_digest: str) -> bool:
        try:
            observed = self._invoke(("docker", "container", "ls", "--all", "--quiet",
                                     "--filter", f"name=^/{name}$"), timeout_seconds=30)
            if observed["returncode"] != 0 or observed["terminated"] is not True:
                return False
            identities = observed["stdout"].split()
            if not identities:
                return True
            if len(identities) != 1:
                return False
            identity = identities[0]
            owner = self._invoke(("docker", "inspect", "--format",
                '{{index .Config.Labels "org.sandbox.activation.init-owner"}}', identity),
                timeout_seconds=30)
            if owner["returncode"] != 0 or owner["terminated"] is not True \
                    or owner["stdout"].strip() != owner_digest:
                return False
            removed = self._invoke(("docker", "rm", "--force", identity), timeout_seconds=30)
            if removed["returncode"] != 0 or removed["terminated"] is not True:
                return False
            absent = self._invoke(("docker", "container", "ls", "--all", "--quiet",
                                   "--filter", f"name=^/{name}$"), timeout_seconds=30)
            return absent["returncode"] == 0 and absent["terminated"] is True \
                and absent["stdout"].strip() == ""
        except Exception:
            return False

    def replace_services(self, *, compose_files: tuple[str, ...], project_name: str,
                         services: tuple[str, ...], exact_image: str,
                         environment_overrides: dict[str, str], timeout_seconds: int) -> None:
        self._image(exact_image)
        if any(key not in {f"SANDBOX_ACTIVATION_IMAGE_{name.upper().replace('-', '_')}"
                           for name in services} for key in environment_overrides):
            raise RemoteActivationError("topology_mismatch")
        if any(value != exact_image for value in environment_overrides.values()):
            raise RemoteActivationError("topology_mismatch")
        if not self._compose_selector or tuple(compose_files) != self._compose_selector.get("compose_files") \
                or project_name != self._compose_selector.get("project_name") \
                or environment_overrides != self._compose_selector.get("environment"):
            raise RemoteActivationError("topology_mismatch")
        argv = ["docker", "compose", "--file", "-", "--project-directory",
                str(self._compose_selector["project_directory"])]
        argv.extend(("--project-name", project_name, "up", "--detach", "--no-build",
                     "--pull", "never", "--no-deps", *map(self._service, services)))
        source = {**self._compose_selector, "kind": "compose_replace_v1",
                  "services": tuple(services)}
        result = self._invoke(tuple(argv), environment=environment_overrides,
                              private_environment_source=source,
                              timeout_seconds=timeout_seconds)
        if result.get("returncode") != 0 or result.get("terminated") is not True:
            raise RemoteActivationError("effect_unknown")
        expected_digest = self._compose_selector["render_digest"]
        self._render_default(compose_files=compose_files, project_name=project_name,
                             selected_services=services,
                             image_overrides={name: exact_image for name in services})
        if self._compose_selector.get("render_digest") != expected_digest:
            raise RemoteActivationError("effect_unknown")

    def replace_services_v2(self, *, compose_files: tuple[str, ...], project_name: str,
                            services: tuple[str, ...], service_image_bindings: dict[str, str],
                            environment_bindings: dict[str, str], snapshot_digest: str,
                            timeout_seconds: int) -> None:
        """Replace every selected service in one no-build/no-pull Compose effect."""
        selector = self._compose_selector_v2
        if (not selector or tuple(compose_files) != selector.get("compose_files")
                or project_name != selector.get("project_name")
                or services != selector.get("selected_services")
                or service_image_bindings != selector.get("service_images")
                or environment_bindings != selector.get("environment")
                or snapshot_digest != selector.get("snapshot_digest")):
            raise RemoteActivationError("topology_mismatch")
        argv = ["docker", "compose", "--file", "-", "--project-directory",
                str(selector["project_directory"]), "--project-name", project_name,
                "up", "--detach", "--no-build", "--pull", "never", "--no-deps",
                *map(self._service, services)]
        source = {**selector["private_source"], "kind": "compose_replace_v2",
                  "services": services, "render_digest": selector["render_digest"],
                  "topology_digest": selector["topology_digest"]}
        result = self._invoke(tuple(argv), timeout_seconds=timeout_seconds,
                              environment=environment_bindings,
                              private_environment_source=source)
        if result.get("returncode") != 0 or result.get("terminated") is not True:
            raise RemoteActivationError("effect_unknown")

    def observe_local_image(self, **selectors) -> dict:
        return self._observe(kind="local", **selectors)

    def observe_running(self, **selectors) -> dict:
        return self._observe(kind="running", **selectors)

    def observe_running_v2(self, *, target: dict, services: tuple[str, ...],
                           compose_project: str, topology_digest: str,
                           compose_config_hashes: dict[str, str],
                           snapshot_digest: str) -> dict:
        """Observe against the retained private-render identity, not labels."""
        selector = self._compose_selector_v2
        if (not selector or services != selector.get("selected_services")
                or compose_project != selector.get("project_name")
                or topology_digest != selector.get("topology_digest")
                or compose_config_hashes != selector.get("compose_config_hashes")
                or snapshot_digest != selector.get("snapshot_digest")
                or target != selector.get("private_source", {}).get("target")):
            raise RemoteActivationError("runtime_mismatch")
        start_epoch = self._observe_default(kind="epoch", target={})["runtime_epoch"]
        start_target = self._observed_target(start_epoch)
        if start_epoch != selector["runtime_epoch"] \
                or start_epoch != target.get("daemon_identity"):
            raise RemoteActivationError("runtime_mismatch")
        source = {**selector["private_source"], "kind": "compose_observe_v2",
                  "services": services, "render_digest": selector["render_digest"],
                  "compose_config_hashes": compose_config_hashes,
                  "topology_digest": topology_digest}
        result = self._invoke(("sandbox-activation-observe-running-v2",
                               self._service(compose_project),
                               *map(self._service, services)), timeout_seconds=60,
                              private_environment_source=source)
        try:
            rows = json.loads(result["stdout"])
        except (TypeError, json.JSONDecodeError):
            raise RemoteActivationError("runtime_mismatch") from None
        if result["returncode"] != 0 or result["terminated"] is not True \
                or type(rows) is not list or len(rows) != len(services):
            raise RemoteActivationError("runtime_mismatch")
        normalized = []
        allowed = {"service", "compose_project", "runtime_identity", "declared_image",
                   "repository_digest", "local_image_id", "config_digest", "platform",
                   "healthy"}
        for row in rows:
            if type(row) is not dict or set(row) != allowed \
                    or row.get("service") not in services \
                    or row.get("compose_project") != compose_project:
                raise RemoteActivationError("runtime_mismatch")
            service = row["service"]
            normalized.append({**row, "topology_identity": topology_digest,
                               "compose_config_hash": compose_config_hashes[service]})
        if {row["service"] for row in normalized} != set(services):
            raise RemoteActivationError("runtime_mismatch")
        end_epoch = self._observe_default(kind="epoch", target={})["runtime_epoch"]
        end_target = self._observed_target(end_epoch)
        return {"target_epoch_start": start_target["machine_identity"],
                "target_epoch_end": end_target["machine_identity"],
                "target_identity_start": start_target["target_identity"],
                "target_identity_end": end_target["target_identity"],
                "runtime_epoch_start": start_epoch, "runtime_epoch_end": end_epoch,
                "services": normalized}
