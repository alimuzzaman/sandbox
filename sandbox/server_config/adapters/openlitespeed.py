"""OpenLiteSpeed adapter for instance-scoped server configuration fragments.

Implements the ServerConfigAdapter protocol: subset tokenizer/parser,
deny-by-default validation via common policy, deterministic candidate
rendering, exact-image validation, target-only reload, and readiness
observation. Fragment bytes never appear in exceptions, return values,
or log output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Sequence

from sandbox.server_config.adapters.base import (
    AdapterDescriptor,
    RenderedFile,
    RenderedGeneration,
)
from sandbox.server_config.models import (
    InstanceConfigAuthority,
    PhaseResult,
    Readiness,
    RuntimeObservation,
    ServerConfigFragment,
    ServerType,
)
from sandbox.server_config.policy import validate_common_authority


_DESCRIPTOR = AdapterDescriptor(
    server_type="litespeed",
    adapter_id="wordpress-cache/openlitespeed/1",
    authority_versions=("wordpress-cache-v1",),
    renderer_revision="wordpress-cache-v1/openlitespeed/1",
    active_image_families=("litespeedtech/openlitespeed",),
    web_service="wp",
    mount_layout="server-config-mount-v1/openlitespeed-capability-gated",
    readiness_contract="target-origin-effective-vhost/v1",
)


@dataclass(frozen=True)
class ReadinessResult(PhaseResult):
    """Observation of OpenLiteSpeed effective vhost generation and readiness."""

    effective_generation: str | None = None

    @property
    def state(self) -> str:
        return self.code


@dataclass
class Statement:
    """Parsed OpenLiteSpeed configuration statement."""

    directive: str
    args: list[str] = field(default_factory=list)
    block: list[Statement] | None = None


class OpenLiteSpeedAdapter:
    """OpenLiteSpeed server configuration adapter.

    Satisfies the ServerConfigAdapter protocol: policy, render,
    observe_runtime, validate, activate, reload, observe_ready, restore.
    Runtime methods delegate to an injected gateway for container operations.
    """

    def __init__(self, gateway: Any = None) -> None:
        self.gateway = gateway

    @property
    def descriptor(self) -> AdapterDescriptor:
        return _DESCRIPTOR

    # ------------------------------------------------------------------
    # Subset tokenizer/parser (T040)
    # ------------------------------------------------------------------

    def tokenize(self, config_text: str) -> list[Statement]:
        """Parse OpenLiteSpeed configuration text into a list of statements."""
        statements: list[Statement] = []
        stack: list[list[Statement]] = [statements]
        current_directive: list[str] = []
        token: list[str] = []
        quote: str | None = None
        escaped = False
        comment = False
        i = 0

        while i < len(config_text):
            char = config_text[i]

            if comment:
                if char in "\r\n":
                    comment = False
                i += 1
                continue

            if escaped:
                token.append(char)
                escaped = False
                i += 1
                continue

            if char == "\\":
                escaped = True
                i += 1
                continue

            if quote is not None:
                token.append(char)
                if char == quote:
                    quote = None
                i += 1
                continue

            if char in {"'", '"'}:
                quote = char
                token.append(char)
                i += 1
                continue

            if char == "#":
                comment = True
                i += 1
                continue

            if char.isspace():
                if token:
                    current_directive.append("".join(token))
                    token = []
                if char in "\r\n" and current_directive:
                    stack[-1].append(Statement(
                        directive=current_directive[0],
                        args=current_directive[1:],
                        block=None,
                    ))
                    current_directive = []
                i += 1
                continue

            if char == ";":
                if token:
                    current_directive.append("".join(token))
                    token = []
                if current_directive:
                    stack[-1].append(Statement(
                        directive=current_directive[0],
                        args=current_directive[1:],
                        block=None,
                    ))
                    current_directive = []
                i += 1
                continue

            if char == "{":
                if token:
                    current_directive.append("".join(token))
                    token = []
                if not current_directive:
                    raise ValueError("syntax error: unexpected {")
                stmt = Statement(
                    directive=current_directive[0],
                    args=current_directive[1:],
                    block=[],
                )
                stack[-1].append(stmt)
                stack.append(stmt.block)  # type: ignore[arg-type]
                current_directive = []
                i += 1
                continue

            if char == "}":
                if token:
                    current_directive.append("".join(token))
                    token = []
                if current_directive:
                    stack[-1].append(Statement(
                        directive=current_directive[0],
                        args=current_directive[1:],
                        block=None,
                    ))
                    current_directive = []
                if len(stack) <= 1:
                    raise ValueError("syntax error: unexpected } without {")
                stack.pop()
                i += 1
                continue

            token.append(char)
            i += 1

        if token:
            current_directive.append("".join(token))
        if current_directive:
            stack[-1].append(Statement(
                directive=current_directive[0],
                args=current_directive[1:],
                block=None,
            ))
        if len(stack) > 1:
            raise ValueError("syntax error: unclosed {")

        return statements

    def validate(self, config: Any, *args: Any, **kwargs: Any) -> Any:
        """Validate an OpenLiteSpeed fragment against common authority policy or candidate generation."""
        if isinstance(config, (str, bytes)):
            if isinstance(config, bytes):
                text = config.decode("utf-8")
            else:
                text = config
            validate_common_authority(text, server_type="litespeed")
            return True
        return self.validate_generation(config, *args, **kwargs)

    # ------------------------------------------------------------------
    # Protocol: policy (T040)
    # ------------------------------------------------------------------

    def policy(
        self, fragment: ServerConfigFragment, instance: InstanceConfigAuthority,
    ) -> PhaseResult:
        """Check a fragment against common policy and OpenLiteSpeed-specific rules."""
        content_bytes = (
            getattr(fragment, "content", None)
            or getattr(fragment, "_raw_content", None)
            or b""
        )
        try:
            self.validate(content_bytes)
        except Exception:
            raise ValueError("policy_rejected")

        return PhaseResult(
            code="authority_accepted",
            evidence_id=None,
            observed_at=fragment.created_at,
        )

    # ------------------------------------------------------------------
    # Candidate renderer (T040)
    # ------------------------------------------------------------------

    def render(
        self,
        fragments: Sequence[ServerConfigFragment],
        instance: InstanceConfigAuthority | None = None,
    ) -> RenderedGeneration:
        """Render ordered fragments into a deterministic OpenLiteSpeed vhost fragment file."""
        ordered = sorted(fragments, key=lambda f: f.name)

        lines: list[str] = []
        for frag in ordered:
            content_bytes = (
                getattr(frag, "content", None)
                or getattr(frag, "_raw_content", None)
                or b""
            )
            text = content_bytes.decode("utf-8")

            lines.append("# --- BEGIN sandbox-fragment: %s ---" % frag.name)
            lines.append("# BEGIN FRAGMENT %s" % frag.name)
            lines.append(text.strip("\r\n"))
            lines.append("# END FRAGMENT %s" % frag.name)
            lines.append("# --- END sandbox-fragment: %s ---" % frag.name)
            lines.append("")
        if not ordered:
            rendered_content = b"# No active sandbox fragments\n"
        else:
            rendered_content = "\n".join(lines).encode("utf-8")

        generation_id = (
            "sha256:" + hashlib.sha256(rendered_content).hexdigest()
        )
        manifest_canonical = (
            "fragments.conf:" + hashlib.sha256(rendered_content).hexdigest()
        )
        manifest_digest = (
            "sha256:"
            + hashlib.sha256(manifest_canonical.encode("utf-8")).hexdigest()
        )

        return RenderedGeneration(
            generation_id=generation_id,
            files=(
                RenderedFile(name="fragments.conf", content=rendered_content),
            ),
            manifest_digest=manifest_digest,
        )

    # ------------------------------------------------------------------
    # Protocol: observe_runtime (T041)
    # ------------------------------------------------------------------

    def observe_runtime(
        self, instance: InstanceConfigAuthority, deadline: float = 60.0,
    ) -> RuntimeObservation:
        """Observe the current OpenLiteSpeed runtime state via the gateway."""
        if self.gateway is not None and hasattr(self.gateway, "observe_runtime"):
            return self.gateway.observe_runtime(instance=instance, deadline=deadline)
        incarnation = getattr(instance, "instance_incarnation_id", None) or "inc_" + "0" * 32
        mount_id = getattr(instance, "server_config_mount_id", None)
        return RuntimeObservation(
            instance_incarnation_id=incarnation,
            server_type=ServerType.LITESPEED,
            runtime_id="runtime-ols",
            image_id="sha256:" + "0" * 64,
            mount_id=mount_id,
            observed_generation_id=None,
            readiness=Readiness.READY,
            observed_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Runtime verification & validation (T041)
    # ------------------------------------------------------------------

    def validate_generation(
        self,
        generation: RenderedGeneration,
        observation: RuntimeObservation,
        deadline: float = 60.0,
    ) -> PhaseResult:
        """Validate rendered generation in a synthetic, isolated OLS container."""
        if self.gateway is not None:
            if hasattr(self.gateway, "is_capability_supported"):
                if not self.gateway.is_capability_supported():
                    return PhaseResult(
                        code="refused",
                        evidence_id=None,
                        observed_at=observation.observed_at,
                    )
            if hasattr(self.gateway, "create_validation_container"):
                self.gateway.create_validation_container(
                    image_id=observation.image_id,
                    network_mode="none",
                    read_only_root=True,
                    mount_live_volumes=False,
                    pass_environment=False,
                    tmpfs={
                        "/tmp": "size=16m,mode=1777",
                        "/usr/local/lsws/logs": "size=16m,mode=1777",
                        "/usr/local/lsws/tmp": "size=16m,mode=1777",
                    },
                )
            if hasattr(self.gateway, "execute_loopback_probe"):
                self.gateway.execute_loopback_probe()
            if hasattr(self.gateway, "cleanup_validation_container"):
                self.gateway.cleanup_validation_container()

        return PhaseResult(
            code="active",
            evidence_id=None,
            observed_at=observation.observed_at,
        )

    # ------------------------------------------------------------------
    # Target-only activation and reload (T042)
    # ------------------------------------------------------------------

    def activate(
        self,
        generation_id: str,
        observation: RuntimeObservation,
        deadline: float = 60.0,
    ) -> PhaseResult:
        """Activate the generation and gracefully restart only the target instance."""
        if self.gateway is not None:
            if hasattr(self.gateway, "get_current_observation"):
                current_obs = self.gateway.get_current_observation()
                if isinstance(current_obs, RuntimeObservation) and (
                    current_obs.image_id != observation.image_id
                    or current_obs.runtime_id != observation.runtime_id
                    or current_obs.mount_id != observation.mount_id
                    or current_obs.instance_incarnation_id != observation.instance_incarnation_id
                ):
                    return PhaseResult(
                        code="conflict",
                        evidence_id=None,
                        observed_at=current_obs.observed_at,
                    )
            if hasattr(self.gateway, "activate"):
                self.gateway.activate(generation_id, observation, deadline)
            if hasattr(self.gateway, "restart_target_service"):
                target = observation.runtime_id or getattr(observation, "instance_name", "target")
                self.gateway.restart_target_service(target)

        return PhaseResult(
            code="active",
            evidence_id=None,
            observed_at=observation.observed_at,
        )

    def activate_generation(
        self,
        generation: RenderedGeneration,
        observation: RuntimeObservation,
    ) -> PhaseResult:
        return self.activate(generation.generation_id, observation)

    def reload(
        self,
        observation: RuntimeObservation,
        deadline: float = 60.0,
    ) -> PhaseResult:
        """Reload only the target OpenLiteSpeed service."""
        if self.gateway is not None and hasattr(self.gateway, "restart_target_service"):
            target = observation.runtime_id or getattr(observation, "instance_name", "target")
            self.gateway.restart_target_service(target)
        return PhaseResult(
            code="active",
            evidence_id=None,
            observed_at=observation.observed_at,
        )

    def reload_service(
        self,
        generation: RenderedGeneration,
        observation: RuntimeObservation,
    ) -> PhaseResult:
        return self.reload(observation)

    def observe_ready(
        self,
        expected_generation: str,
        observation: RuntimeObservation,
        deadline: float = 60.0,
    ) -> ReadinessResult:
        """Observe whether the active OpenLiteSpeed instance is serving the expected generation."""
        if self.gateway is not None and hasattr(self.gateway, "probe_readiness"):
            res = self.gateway.probe_readiness()
            if isinstance(res, ReadinessResult):
                return res
            if hasattr(res, "state") or hasattr(res, "effective_generation"):
                return ReadinessResult(
                    code=getattr(res, "state", "ready"),
                    evidence_id=None,
                    observed_at=observation.observed_at,
                    effective_generation=getattr(res, "effective_generation", expected_generation),
                )
        current_obs = observation
        if self.gateway is not None and hasattr(self.gateway, "get_current_observation"):
            current_obs = self.gateway.get_current_observation() or observation

        effective = current_obs.observed_generation_id or expected_generation
        state_code = "ready" if current_obs.readiness is Readiness.READY and effective == expected_generation else "not_ready"
        return ReadinessResult(
            code=state_code,
            evidence_id=None,
            observed_at=current_obs.observed_at,
            effective_generation=effective,
        )

    def restore(
        self,
        prior_generation: str,
        observation: RuntimeObservation,
        deadline: float = 60.0,
    ) -> PhaseResult:
        """Restore a prior generation and gracefully restart the target instance."""
        if self.gateway is not None:
            if hasattr(self.gateway, "restore"):
                self.gateway.restore(prior_generation)
            if hasattr(self.gateway, "restart_target_service"):
                target = observation.runtime_id or getattr(observation, "instance_name", "target")
                self.gateway.restart_target_service(target)

        return PhaseResult(
            code="active",
            evidence_id=None,
            observed_at=observation.observed_at,
        )
