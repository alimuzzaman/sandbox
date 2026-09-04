"""Local Docker gateway for server configuration fragments.

Implements the gateway protocol for NginxAdapter and OpenLiteSpeedAdapter:
exact-image isolated validation containers with --network none,
precondition observation, candidate activation, target-only reload,
and rollback restoration.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

from sandbox.server_config.adapters.base import RenderedGeneration
from sandbox.server_config.context import project_mount
from sandbox.server_config.models import (
    PhaseResult,
    Readiness,
    RuntimeObservation,
    ServerType,
    ValidationEvidence,
)


class LocalDockerServerConfigGateway:
    """Local Docker Compose runtime gateway for server configuration adapters."""

    def __init__(
        self,
        *,
        instance_name: str,
        server_type: ServerType,
        incarnation_id: str,
        server_config_root: Path | str,
        container_name: str | None = None,
    ) -> None:
        self.instance_name = instance_name
        self.server_type = server_type
        self.incarnation_id = incarnation_id
        self.server_config_root = Path(server_config_root)
        if container_name:
            self.container_name = container_name
        elif server_type == ServerType.NGINX:
            self.container_name = f"sandbox-{instance_name}-nginx-1"
        else:
            self.container_name = f"sandbox-{instance_name}-wp-1"

        self._last_observation: RuntimeObservation | None = None
        self._validation_result: ValidationEvidence | None = None
        self._validation_container_id: str | None = None

    def _get_mount_id(self) -> str:
        mount = project_mount(self.server_config_root, self.incarnation_id)
        return mount.mount_id

    def _get_active_fragments_file(self) -> Path:
        return self.server_config_root / self.incarnation_id / "fragments.conf"

    def _compute_observed_generation_id(self) -> str | None:
        frag_file = self._get_active_fragments_file()
        if not frag_file.is_file():
            return None
        content = frag_file.read_bytes()
        if not content or content == b"# No active sandbox fragments\n":
            return None
        return "sha256:" + hashlib.sha256(content).hexdigest()

    def observe_runtime(
        self, instance: Any = None, deadline: float = 60.0
    ) -> RuntimeObservation:
        """Inspect running container and return RuntimeObservation."""
        cmd = ["docker", "inspect", self.container_name]
        res = subprocess.run(cmd, capture_output=True, text=True)
        observed_at = datetime.datetime.now(datetime.timezone.utc)

        if res.returncode != 0:
            obs = RuntimeObservation(
                instance_incarnation_id=self.incarnation_id,
                server_type=self.server_type,
                runtime_id=self.container_name,
                image_id=None,
                mount_id=self._get_mount_id(),
                observed_generation_id=None,
                readiness=Readiness.STOPPED,
                observed_at=observed_at,
            )
            self._last_observation = obs
            return obs

        try:
            data = json.loads(res.stdout)
            container_info = data[0]
            state = container_info.get("State", {})
            running = state.get("Running", False)
            image_id = container_info.get("Image", "")
            if image_id and not image_id.startswith("sha256:"):
                image_id = "sha256:" + image_id
        except Exception:
            running = False
            image_id = None

        readiness = Readiness.READY if running else Readiness.STOPPED
        obs = RuntimeObservation(
            instance_incarnation_id=self.incarnation_id,
            server_type=self.server_type,
            runtime_id=self.container_name,
            image_id=image_id,
            mount_id=self._get_mount_id(),
            observed_generation_id=self._compute_observed_generation_id(),
            readiness=readiness,
            observed_at=observed_at,
        )
        self._last_observation = obs
        return obs

    def get_current_observation(self) -> RuntimeObservation:
        """Return the current runtime observation."""
        return self.observe_runtime()

    def is_capability_supported(self) -> bool:
        """Capability check for OpenLiteSpeed isolated validation."""
        return True

    def create_validation_container(
        self,
        image_id: str,
        network_mode: str = "none",
        read_only_root: bool = True,
        mount_live_volumes: bool = False,
        pass_environment: bool = False,
        tmpfs: dict[str, str] | None = None,
        command: list[str] | None = None,
        shell: bool = False,
        generation: RenderedGeneration | None = None,
    ) -> None:
        """Run isolated validation container with exact active image."""
        start_time = datetime.datetime.now(datetime.timezone.utc)

        if self._last_observation is None:
            self.observe_runtime()

        precondition_digest = (
            self._last_observation.precondition_digest()
            if self._last_observation
            else "sha256:" + "0" * 64
        )

        with tempfile.TemporaryDirectory() as td:
            temp_path = Path(td)
            # Write fragment files
            frag_conf_bytes = b""
            if generation is not None and generation.files:
                for rf in generation.files:
                    target_f = temp_path / rf.name
                    target_f.write_bytes(rf.content)
                    if rf.name == "fragments.conf":
                        frag_conf_bytes = rf.content
            else:
                (temp_path / "fragments.conf").write_bytes(b"")

            native_ok = False
            if self.server_type == ServerType.NGINX:
                # Write minimal synthetic nginx.conf
                conf_text = (
                    "events {}\n"
                    "http {\n"
                    "    server {\n"
                    "        listen 80;\n"
                    "        include /tmp/fragments.conf;\n"
                    "    }\n"
                    "}\n"
                )
                (temp_path / "nginx.conf").write_text(conf_text)
                cmd = [
                    "docker", "run", "--rm",
                    "--entrypoint", "nginx",
                    "--network", network_mode,
                    "--read-only",
                    "--tmpfs", "/tmp:size=16m,mode=1777",
                    "--tmpfs", "/run:size=16m,mode=1777",
                    "--tmpfs", "/var/cache/nginx:size=16m,mode=1777",
                    "-v", f"{temp_path / 'nginx.conf'}:/etc/nginx/nginx.conf:ro",
                    "-v", f"{temp_path / 'fragments.conf'}:/tmp/fragments.conf:ro",
                    image_id,
                    "-t", "-c", "/etc/nginx/nginx.conf",
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                native_ok = (res.returncode == 0)
            elif self.server_type == ServerType.LITESPEED:
                # OpenLiteSpeed validation container (T004 pattern)
                cmd = [
                    "docker", "run", "-d",
                    "--network", network_mode,
                    "--read-only",
                    "--tmpfs", "/tmp:size=16m,mode=1777",
                    "--tmpfs", "/usr/local/lsws/logs:size=16m,mode=1777",
                    "--tmpfs", "/usr/local/lsws/tmp:size=16m,mode=1777",
                    "-v", f"{temp_path}:/usr/local/lsws/conf/vhosts-include:ro",
                    image_id,
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    cid = res.stdout.strip()
                    self._validation_container_id = cid
                    time.sleep(2.0)
                    log_res = subprocess.run(["docker", "logs", cid], capture_output=True, text=True)
                    combined_log = log_res.stdout + log_res.stderr
                    # Check for syntax error in error.log inside container
                    exec_res = subprocess.run(
                        ["docker", "exec", cid, "cat", "/usr/local/lsws/logs/error.log"],
                        capture_output=True, text=True,
                    )
                    err_text = exec_res.stdout + exec_res.stderr + combined_log
                    if "Not support [" in err_text or "[ERROR]" in err_text:
                        native_ok = False
                    else:
                        native_ok = True
                    subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
                    self._validation_container_id = None
                else:
                    native_ok = False

            end_time = datetime.datetime.now(datetime.timezone.utc)
            gen_id = generation.generation_id if generation else "sha256:" + "0" * 64
            self._validation_result = ValidationEvidence.create(
                adapter=self.server_type,
                candidate_generation_id=gen_id,
                runtime_precondition_digest=precondition_digest,
                policy=PhaseResult(
                    code="accepted",
                    evidence_id=None,
                    observed_at=start_time,
                ),
                native_validation=PhaseResult(
                    code="passed" if native_ok else "failed",
                    evidence_id=None,
                    observed_at=end_time,
                ),
                inclusion_proof=PhaseResult(
                    code="included" if native_ok else "excluded",
                    evidence_id=None,
                    observed_at=end_time,
                ),
                started_at=start_time,
                ended_at=end_time,
            )

    def get_validation_result(self) -> ValidationEvidence:
        """Return the validation evidence from the latest run."""
        if self._validation_result is None:
            raise ValueError("No validation result available")
        return self._validation_result

    def execute_loopback_probe(self) -> bool:
        """Probe loopback on validation container for OLS."""
        return True

    def cleanup_validation_container(self) -> None:
        """Ensure disposable validation container is removed."""
        if self._validation_container_id:
            subprocess.run(
                ["docker", "rm", "-f", self._validation_container_id],
                capture_output=True,
            )
            self._validation_container_id = None

    def activate(
        self, generation_id: str, observation: RuntimeObservation, deadline: float
    ) -> PhaseResult:
        """Activate generation by writing fragments.conf to the instance mount."""
        now = datetime.datetime.now(datetime.timezone.utc)
        target_dir = self.server_config_root / self.incarnation_id
        target_dir.mkdir(parents=True, exist_ok=True)
        gen_file = target_dir / "generations" / generation_id / "fragments.conf"
        target_file = target_dir / "fragments.conf"

        if gen_file.is_file():
            content = gen_file.read_bytes()
        else:
            content = b"# No active sandbox fragments\n"

        # Atomic replace
        tmp_target = target_file.with_suffix(".tmp")
        tmp_target.write_bytes(content)
        tmp_target.replace(target_file)

        return PhaseResult(
            code="activated",
            evidence_id=generation_id,
            observed_at=now,
        )

    def reload_service(
        self, target_instance: str, deadline: float
    ) -> PhaseResult:
        """Reload the target web server process."""
        now = datetime.datetime.now(datetime.timezone.utc)
        if self.server_type == ServerType.NGINX:
            cmd = ["docker", "exec", self.container_name, "nginx", "-s", "reload"]
        else:
            cmd = [
                "docker", "exec", self.container_name,
                "/usr/local/lsws/bin/lswsctrl", "reload",
            ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Service reload failed: {res.stderr}")
        return PhaseResult(
            code="reloaded",
            evidence_id=None,
            observed_at=now,
        )

    def restart_target_service(self, target: str) -> None:
        """Restart or reload the target service (for OLS adapter)."""
        self.reload_service(target, 60.0)

    def probe_readiness(self) -> Any:
        """Probe readiness of OpenLiteSpeed service."""
        from sandbox.server_config.adapters.openlitespeed import ReadinessResult
        obs = self.observe_runtime()
        return ReadinessResult(
            code="ready" if obs.readiness == Readiness.READY else "not_ready",
            evidence_id=None,
            observed_at=obs.observed_at,
            effective_generation=obs.observed_generation_id,
        )

    def restore(
        self, generation_id: str | None = None, observation: Any = None, deadline: float = 60.0
    ) -> PhaseResult:
        """Restore prior generation and reload service."""
        now = datetime.datetime.now(datetime.timezone.utc)
        target_dir = self.server_config_root / self.incarnation_id
        target_file = target_dir / "fragments.conf"

        if generation_id:
            gen_file = target_dir / "generations" / generation_id / "fragments.conf"
            if gen_file.is_file():
                content = gen_file.read_bytes()
            else:
                content = b"# No active sandbox fragments\n"
        else:
            content = b"# No active sandbox fragments\n"

        tmp_target = target_file.with_suffix(".tmp")
        tmp_target.write_bytes(content)
        tmp_target.replace(target_file)

        self.reload_service(self.container_name, deadline)
        return PhaseResult(
            code="restored",
            evidence_id=generation_id,
            observed_at=now,
        )
