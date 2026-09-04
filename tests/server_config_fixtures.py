"""Pure, deterministic Feature 053 test builders.

These helpers never inspect the process environment or contact a runtime. Fixture
content is synthetic, non-secret configuration data for parser/service tests only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib

from sandbox.server_config.adapters.base import AdapterDescriptor
from sandbox.server_config.models import (
    FragmentSet,
    Readiness,
    RuntimeObservation,
    ServerConfigFragment,
    ServerType,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "server_config"
FIXED_NOW = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
FIXED_INCARNATION = "inc_" + "1" * 32


@dataclass
class FakeClock:
    current: datetime = FIXED_NOW
    monotonic_seconds: float = 1_000.0

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.monotonic_seconds

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
        self.monotonic_seconds += seconds


def fixture_bytes(server_type: str, kind: str) -> bytes:
    return (FIXTURE_ROOT / server_type / f"{kind}.conf").read_bytes()


def fragment(
    *,
    name: str = "page-cache",
    server_type: ServerType = ServerType.NGINX,
    content: bytes | None = None,
) -> ServerConfigFragment:
    payload = content if content is not None else fixture_bytes(server_type.value, "valid")
    return ServerConfigFragment.create(
        name=name,
        authority="wordpress-cache-v1",
        server_type=server_type,
        content=payload,
        content_locator=(
            "fragments/" + hashlib.sha256(payload).hexdigest() + ".fragment"
        ),
        instance_incarnation_id=FIXED_INCARNATION,
        created_at=FIXED_NOW,
        policy_revision=f"wordpress-cache-v1/{server_type.value}/1",
    )


def fragment_set(
    *items: ServerConfigFragment,
    server_type: ServerType = ServerType.NGINX,
) -> FragmentSet:
    return FragmentSet.create(
        instance_incarnation_id=FIXED_INCARNATION,
        server_type=server_type,
        fragments=items,
        renderer_revision=f"{server_type.value}/1",
        rendered_generation_id="sha256:" + "a" * 64,
        created_at=FIXED_NOW,
    )


def runtime_observation(
    *,
    server_type: ServerType = ServerType.NGINX,
    readiness: Readiness = Readiness.READY,
) -> RuntimeObservation:
    return RuntimeObservation(
        instance_incarnation_id=FIXED_INCARNATION,
        server_type=server_type,
        runtime_id="runtime-1",
        image_id="sha256:" + "a" * 64,
        mount_id="sha256:" + "b" * 64,
        observed_generation_id="sha256:" + "c" * 64,
        readiness=readiness,
        observed_at=FIXED_NOW,
    )


@dataclass
class FakeAdapter:
    """Call-recording adapter double with explicit, injected phase results."""

    descriptor: AdapterDescriptor
    results: dict[str, object] = field(default_factory=dict)
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    def _result(self, phase: str, *arguments: object) -> object:
        self.calls.append((phase, arguments))
        result = self.results.get(phase)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(*arguments)
        return result

    def policy(self, fragment, instance):
        return self._result("policy", fragment, instance)

    def render(self, fragments, instance):
        return self._result("render", fragments, instance)

    def observe_runtime(self, instance, deadline):
        return self._result("observe_runtime", instance, deadline)

    def validate(self, generation, observation, deadline):
        return self._result("validate", generation, observation, deadline)

    def activate(self, generation_id, observation, deadline):
        return self._result("activate", generation_id, observation, deadline)

    def reload(self, observation, deadline):
        return self._result("reload", observation, deadline)

    def observe_ready(self, generation_id, observation, deadline):
        return self._result("observe_ready", generation_id, observation, deadline)

    def restore(self, generation_id, observation, deadline):
        return self._result("restore", generation_id, observation, deadline)
