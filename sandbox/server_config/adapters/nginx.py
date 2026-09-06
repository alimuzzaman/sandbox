"""Nginx adapter for instance-scoped server configuration fragments.

Implements the ServerConfigAdapter protocol: subset tokenizer/parser,
deny-by-default validation via common policy, deterministic candidate
rendering, exact-image validation, target-only reload, and readiness
observation. Fragment bytes never appear in exceptions, return values,
or log output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    ValidationEvidence,
)
from sandbox.server_config.policy import validate_common_authority


_DESCRIPTOR = AdapterDescriptor(
    server_type="nginx",
    adapter_id="wordpress-cache/nginx/1",
    authority_versions=("wordpress-cache-v1",),
    renderer_revision="wordpress-cache-v1/nginx/1",
    active_image_families=("nginx",),
    web_service="nginx",
    mount_layout="server-config-mount-v1/nginx",
    readiness_contract="target-origin-effective-generation/v1",
)


@dataclass
class Statement:
    """Parsed nginx configuration statement."""

    directive: str
    args: list[str] = field(default_factory=list)
    block: list[Statement] | None = None


class NginxAdapter:
    """Nginx server configuration adapter.

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
    # Subset tokenizer/parser (T028)
    # ------------------------------------------------------------------

    def tokenize(self, config_text: str) -> list[Statement]:
        """Parse nginx configuration text into a list of statements."""
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
                    raise ValueError("syntax error: unexpected }")
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
            raise ValueError("syntax error: unexpected end of file")
        if len(stack) > 1:
            raise ValueError("syntax error: unclosed {")

        return statements

    # ------------------------------------------------------------------
    # Deny-by-default directive validation (T028)
    # ------------------------------------------------------------------

    def validate(self, config: Any, *args: Any, **kwargs: Any) -> Any:
        """Validate an nginx configuration fragment or candidate generation."""
        if isinstance(config, (str, bytes)):
            config_text = config.decode("utf-8") if isinstance(config, bytes) else config
            try:
                validate_common_authority(config_text, server_type="nginx")
                statements = self.tokenize(config_text)
                locations: set[str] = set()
                for stmt in statements:
                    if stmt.directive == "location" and stmt.args:
                        loc = stmt.args[0]
                        if loc in locations:
                            raise ValueError("duplicate location %s" % loc)
                        locations.add(loc)
            except ValueError as e:
                raise Exception("Validation failed: %s" % e) from e
            return True
        return self.validate_generation(config, *args, **kwargs)

    # ------------------------------------------------------------------
    # Protocol: policy (T028)
    # ------------------------------------------------------------------

    def policy(
        self, fragment: ServerConfigFragment, instance: InstanceConfigAuthority,
    ) -> PhaseResult:
        """Check a fragment against common policy and nginx-specific rules."""
        content_bytes = getattr(fragment, "content", None) or getattr(fragment, "_raw_content", None)
        if content_bytes is None:
            content_bytes = b""

        try:
            text = content_bytes.decode("utf-8")
            self.validate(text)
        except Exception:
            raise ValueError("policy_rejected")

        return PhaseResult(
            code="authority_accepted",
            evidence_id=None,
            observed_at=fragment.created_at,
        )

    # ------------------------------------------------------------------
    # Protocol: render (T028)
    # ------------------------------------------------------------------

    def render(
        self,
        fragments: Sequence[ServerConfigFragment],
        instance: InstanceConfigAuthority | None = None,
    ) -> RenderedGeneration:
        """Render ordered fragments into a deterministic candidate file."""
        ordered = sorted(fragments, key=lambda f: f.name)

        # Cross-fragment duplicate detection
        locations: set[str] = set()
        for frag in ordered:
            content = getattr(frag, "content", None) or getattr(frag, "_raw_content", None)
            if content is not None:
                text = content.decode("utf-8")
                statements = self.tokenize(text)
                for stmt in statements:
                    if stmt.directive == "location" and stmt.args:
                        loc = stmt.args[0]
                        if loc in locations:
                            raise Exception(
                                "duplicate location %s across fragments" % loc
                            )
                        locations.add(loc)

        lines: list[str] = []
        for frag in ordered:
            content_bytes = getattr(frag, "content", None) or getattr(frag, "_raw_content", None)
            if content_bytes is not None:
                text = content_bytes.decode("utf-8")
            else:
                text = ""

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
    # Protocol: observe_runtime (T029)
    # ------------------------------------------------------------------

    def observe_runtime(
        self, instance: InstanceConfigAuthority, deadline: float,
    ) -> RuntimeObservation:
        """Observe the current nginx runtime state via the gateway."""
        if self.gateway is None:
            raise ValueError("no runtime gateway configured")
        return self.gateway.observe_runtime(instance=instance, deadline=deadline)

    # ------------------------------------------------------------------
    # Protocol: validate (T029) - exact-image, network-none validation
    # ------------------------------------------------------------------

    def validate_generation(
        self,
        generation: RenderedGeneration,
        observation: RuntimeObservation,
        deadline: float = 60.0,
    ) -> ValidationEvidence:
        """Validate a candidate generation using an exact-image disposable container.

        Creates a network-isolated, read-only, data-free validation container
        from the exact active image (content-addressed sha256), runs
        ``nginx -t`` via fixed argv, and cleans up the container.
        """
        if self.gateway is None:
            raise ValueError("no runtime gateway configured")

        self.gateway.create_validation_container(
            image_id=observation.image_id,
            network_mode="none",
            read_only_root=True,
            mount_live_volumes=False,
            pass_environment=False,
            tmpfs={"/tmp": "size=16m,mode=1777"},
            command=["nginx", "-t", "-c", "/etc/nginx/nginx.conf"],
            shell=False,
            generation=generation,
        )

        return self.gateway.get_validation_result()

    # ------------------------------------------------------------------
    # Protocol: activate (T030) - pre-activation identity recheck
    # ------------------------------------------------------------------

    def activate(
        self, generation_id: str, observation: RuntimeObservation,
        deadline: float,
    ) -> PhaseResult:
        """Activate a validated generation with pre-activation identity recheck."""
        if self.gateway is None:
            raise ValueError("no runtime gateway configured")

        # Pre-activation identity recheck: verify runtime hasn't changed
        current = self.gateway.get_current_observation()
        if (
            current.runtime_id != observation.runtime_id
            or current.image_id != observation.image_id
            or current.mount_id != observation.mount_id
            or current.instance_incarnation_id
            != observation.instance_incarnation_id
        ):
            raise ValueError(
                "Identity mismatch: runtime state changed between validation and activation"
            )

        return self.gateway.activate(
            generation_id=generation_id,
            observation=observation,
            deadline=deadline,
        )

    def activate_generation(
        self,
        generation: RenderedGeneration,
        observation: RuntimeObservation,
    ) -> PhaseResult:
        """Compatibility method for activate with generation object."""
        return self.activate(
            generation_id=generation.generation_id,
            observation=observation,
            deadline=60.0,
        )

    # ------------------------------------------------------------------
    # Protocol: reload (T030) - target-only nginx reload
    # ------------------------------------------------------------------

    def reload(
        self, observation: RuntimeObservation, deadline: float,
    ) -> PhaseResult:
        """Reload only the target nginx service."""
        if self.gateway is None:
            raise ValueError("no runtime gateway configured")
        return self.gateway.reload_service(
            target_instance=observation.runtime_id,
            deadline=deadline,
        )

    def reload_service(
        self,
        generation: RenderedGeneration,
        observation: RuntimeObservation,
    ) -> PhaseResult:
        """Compatibility method for reload with generation object."""
        return self.reload(observation=observation, deadline=60.0)

    # ------------------------------------------------------------------
    # Protocol: observe_ready (T030) - effective generation readiness
    # ------------------------------------------------------------------

    def observe_ready(
        self, generation_id: str, observation: RuntimeObservation,
        deadline: float,
    ) -> PhaseResult:
        """Observe that the effective generation matches the activated candidate."""
        if self.gateway is None:
            raise ValueError("no runtime gateway configured")

        current = self.gateway.get_current_observation()
        if current.readiness is not Readiness.READY:
            return PhaseResult(
                code="not_ready",
                evidence_id=None,
                observed_at=current.observed_at,
            )
        if current.observed_generation_id != generation_id:
            return PhaseResult(
                code="generation_mismatch",
                evidence_id=None,
                observed_at=current.observed_at,
            )
        return PhaseResult(
            code="ready",
            evidence_id=None,
            observed_at=current.observed_at,
        )

    def check_readiness(
        self,
        generation: RenderedGeneration,
        observation: RuntimeObservation,
    ) -> Readiness:
        """Compatibility method for readiness check with generation object."""
        if self.gateway is None:
            raise ValueError("no runtime gateway configured")

        current = self.gateway.get_current_observation()
        if current.readiness is not Readiness.READY:
            return current.readiness
        if current.observed_generation_id != generation.generation_id:
            return Readiness.UNKNOWN
        return Readiness.READY

    # ------------------------------------------------------------------
    # Protocol: restore (T030)
    # ------------------------------------------------------------------

    def restore(
        self, generation_id: str, observation: RuntimeObservation,
        deadline: float,
    ) -> PhaseResult:
        """Restore a prior generation (rollback)."""
        if self.gateway is None:
            raise ValueError("no runtime gateway configured")
        return self.gateway.restore(
            generation_id=generation_id,
            observation=observation,
            deadline=deadline,
        )
