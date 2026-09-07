"""Linux acceptance canary for host-backed Compose secret ownership.

This module is intentionally outside the default unit-test discovery. Run it on
the Linux host that owns the candidate files through a bounded Sandbox durable
job, passing an already-present immutable image digest and the observed
application UID/GID explicitly. It never pulls an image or prints the synthetic
secret bytes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile
import unittest
import uuid

from tests.subprocess_support import run_test_process, synthetic_environment


_IMAGE = re.compile(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}\Z")
_MAX_UID = 65534
_CANARY_VALUE = "synthetic-hosted-secret-canary"


@dataclass(frozen=True)
class CanaryConfig:
    image: str
    uid: int
    gid: int
    source: str


class HostedSecretAccessCanaryTests(unittest.TestCase):
    """Prove the file owner seen by a Linux container can read its secret."""

    config: CanaryConfig | None = None
    compose_version: str = "unknown"

    def _config(self) -> CanaryConfig:
        if type(self.config) is not CanaryConfig:
            self.fail("canary requires explicit --image, --uid, and --gid")
        return self.config

    def _docker(self) -> str:
        if not sys_platform_linux():
            self.fail("host-backed secret canary requires Linux")
        docker = shutil.which("docker")
        if not docker:
            self.fail("Docker CLI is unavailable")
        docker_env = synthetic_environment({
            "PATH": f"{Path(docker).parent}:/usr/bin:/bin"
        })
        probe = run_test_process(
            (docker, "version", "--format", "{{.Server.Version}}"),
            env=docker_env,
            timeout=15,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0 or not probe.stdout.strip():
            self.fail("Docker daemon is unavailable")
        version = run_test_process(
            (docker, "compose", "version", "--short"), env=docker_env,
            timeout=15, capture_output=True, text=True)
        if version.returncode != 0 or not version.stdout.strip():
            self.fail("Docker Compose is unavailable")
        self.compose_version = version.stdout.strip()
        return docker

    def _env(self, docker: str) -> dict[str, str]:
        return synthetic_environment({
            "PATH": f"{Path(docker).parent}:/usr/bin:/bin"
        })

    def _result_detail(self, label: str, result: object) -> str:
        def bounded(value: object) -> str:
            text = str(value or "").replace(_CANARY_VALUE, "[redacted]")
            return text if len(text) <= 2048 else text[:2048] + "...[truncated]"
        return (f"{label} (compose={self.compose_version}, "
                f"returncode={getattr(result, 'returncode', '?')}, "
                f"stdout={bounded(getattr(result, 'stdout', ''))!r}, "
                f"stderr={bounded(getattr(result, 'stderr', ''))!r})")

    def _image(self, docker: str) -> str:
        image = self._config().image
        inspected = run_test_process(
            (docker, "image", "inspect", image),
            env=self._env(docker),
            timeout=15,
            capture_output=True,
            text=True,
        )
        if inspected.returncode != 0:
            self.fail("the requested immutable canary image is not present locally")
        return image

    def _read_digest(self, docker: str, image: str, secret: Path,
                     uid: int, gid: int, container_name: str, expected: str):
        # sha256sum reads the file but emits only the digest; the canary value
        # never appears in argv, stdout, stderr, or the failure message.
        check = (
            "set -eu; "
            "actual=$(sha256sum /run/secrets/token | cut -d ' ' -f1); "
            f"test \"$actual\" = {expected}"
        )
        try:
            return run_test_process(
                (docker, "run", "--name", container_name, "--rm", "--pull", "never",
                 "--network", "none", "--read-only", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges", "--no-healthcheck",
                 "--restart", "no", "--label", f"sandbox.secret-canary={container_name}",
                 "--user", f"{uid}:{gid}", "--entrypoint", "/bin/sh",
                 "--mount", f"type=bind,src={secret},dst=/run/secrets/token,readonly",
                 image, "-c", check),
                env=self._env(docker),
                timeout=30,
                capture_output=True,
                text=True,
            )
        finally:
            # --rm handles normal completion. After a timeout or daemon-side
            # failure, inspect the exact generated label before forced removal.
            identity = run_test_process(
                (docker, "inspect", "--format",
                 '{{ index .Config.Labels "sandbox.secret-canary" }}', container_name),
                env=self._env(docker),
                timeout=15,
                capture_output=True,
                text=True,
            )
            if identity.returncode == 0:
                if identity.stdout.strip() != container_name:
                    self.fail("canary cleanup identity is unproven")
                removed = run_test_process(
                    (docker, "rm", "--force", container_name),
                    env=self._env(docker),
                    timeout=15,
                    capture_output=True,
                    text=True,
                )
                if removed.returncode != 0:
                    self.fail("canary cleanup failed")
            # A successful, empty label query is the only accepted absence
            # proof. An inspect error alone is ambiguous (for example, the
            # daemon may have become unavailable), so never treat it as clean.
            absent = run_test_process(
                (docker, "ps", "--all", "--quiet", "--filter",
                 f"label=sandbox.secret-canary={container_name}"),
                env=self._env(docker),
                timeout=15,
                capture_output=True,
                text=True,
            )
            if absent.returncode != 0 or absent.stdout.strip():
                self.fail("canary cleanup is unproven")

    def _compose_secret(self, docker: str, *, image: str, root: Path,
                        env_file: Path, run_uid: int, run_gid: int,
                        secret_uid: int, secret_gid: int, project: str,
                        expected: str) -> object:
        compose_file = root / f"{project}.yml"
        compose_file.write_text(
            "services:\n"
            "  web:\n"
            f"    image: {image}\n"
            f"    user: \"{run_uid}:{run_gid}\"\n"
            "    entrypoint: /bin/sh\n"
            "    command: [\"-ec\", "
            "\"test \\\"$$(stat -c '%u %g %a' /run/secrets/token)\\\" = "
            f"\\\"{secret_uid} {secret_gid} 400\\\"; "
            "actual=$$(sha256sum /run/secrets/token | cut -d ' ' -f1); "
            f"test \\\"$$actual\\\" = {expected}\"]\n"
            "    network_mode: none\n"
            "    cap_drop: [ALL]\n"
            "    security_opt: [no-new-privileges]\n"
            "    secrets:\n"
            "      - source: token\n"
            "        target: token\n"
            f"        uid: \"{secret_uid}\"\n"
            f"        gid: \"{secret_gid}\"\n"
            "        mode: 0400\n"
            "secrets:\n"
            "  token:\n"
            "    environment: SANDBOX_CANARY_TOKEN\n"
        )
        command = (docker, "compose", "--env-file", str(env_file), "--file",
                   str(compose_file), "--project-directory", str(root),
                   "--project-name", project, "up", "--abort-on-container-exit",
                   "--exit-code-from", "web", "--pull", "never", "--no-build")
        try:
            return run_test_process(
                command,
                env={**self._env(docker), "SANDBOX_CANARY_TOKEN": _CANARY_VALUE},
                timeout=60,
                capture_output=True,
                text=True,
            )
        finally:
            down = run_test_process(
                (docker, "compose", "--file", str(compose_file),
                 "--project-directory", str(root), "--project-name", project,
                 "down", "--remove-orphans"),
                env={**self._env(docker), "SANDBOX_CANARY_TOKEN": _CANARY_VALUE},
                timeout=30,
                capture_output=True,
                text=True,
            )
            remaining = run_test_process(
                (docker, "ps", "--all", "--quiet", "--filter",
                 f"label=com.docker.compose.project={project}"),
                env=self._env(docker),
                timeout=15,
                capture_output=True,
                text=True,
            )
            if down.returncode != 0 or remaining.returncode != 0 or remaining.stdout.strip():
                self.fail("environment-backed canary cleanup is unproven")

    def _archive_secret(self, docker: str, *, image: str, uid: int, gid: int,
                        secret_uid: int, secret_gid: int, expected: str,
                        container_name: str, expect_success: bool = True) -> object:
        from sandbox.hosting.images.activation.private_secret_files import (
            prepare_container_secret_files, secret_file_mounts,
            verify_container_secret_files,
        )

        label = f"sandbox.secret-canary={container_name}"
        document = {"services": {"web": {"secrets": [{
            "source": "token", "target": "token", "uid": str(secret_uid),
            "gid": str(secret_gid), "mode": "0400"}]}},
            "secrets": {"token": {"environment": "SANDBOX_ACTIVATION_SECRET_0"}}}
        # secret_file_mounts expects base64 material; keep the byte value private.
        import base64
        mounts = secret_file_mounts(
            document, "web", {"token": base64.b64encode(_CANARY_VALUE.encode()).decode()})
        command_text = (
            "set -eu; "
            f"test \"$(stat -c '%u %g %a' /run/secrets/token)\" = \"{secret_uid} {secret_gid} 400\"; "
            "actual=$(sha256sum /run/secrets/token | cut -d ' ' -f1); "
            f"test \"$actual\" = {expected}"
        )
        created = run_test_process(
            (docker, "create", "--name", container_name, "--pull", "never",
             "--network", "none", "--cap-drop", "ALL",
             "--security-opt", "no-new-privileges", "--label", label,
             "--user", f"{uid}:{gid}", "--entrypoint", "/bin/sh", image,
             "-ec", command_text),
            env=self._env(docker), timeout=30, capture_output=True, text=True)
        if created.returncode != 0 or not re.fullmatch(r"[0-9a-f]{64}", created.stdout.strip()):
            self.fail(self._result_detail("archive canary container creation", created))
        identity = created.stdout.strip()

        from sandbox.hosting.images.activation.private_graph import graph_command_port
        command, _deadline = graph_command_port(self._env(docker), 90)

        def inspect(value):
            rows = json.loads(command(["docker", "inspect", value]))
            if type(rows) is not list or len(rows) != 1:
                raise ValueError("archive canary inspect failed")
            return rows[0]

        try:
            try:
                prepared = prepare_container_secret_files(identity, mounts, command, inspect)
            except ValueError:
                from sandbox.hosting.images.activation.private_secret_files import _read_secret_archive
                root_metadata, files = _read_secret_archive(identity, command)
                metadata = [{key: value for key, value in row.items() if key != "data"}
                            | {"bytes_match": row["data"] == _CANARY_VALUE.encode()} for row in files]
                self.fail(f"archive preparation refused: root={root_metadata}, files={metadata}")
            self.assertEqual(prepared, "prepared")
            self.assertTrue(verify_container_secret_files(identity, mounts, command, inspect))
            started = run_test_process((docker, "start", identity), env=self._env(docker),
                                       timeout=30, capture_output=True, text=True)
            waited = run_test_process((docker, "wait", identity), env=self._env(docker),
                                      timeout=30, capture_output=True, text=True)
            self.assertEqual(started.returncode, 0, self._result_detail("archive canary start", started))
            self.assertEqual(waited.returncode, 0, self._result_detail("archive canary wait", waited))
            self.assertRegex(waited.stdout.strip(), r"^[0-9]{1,3}$")
            if expect_success:
                self.assertEqual(waited.stdout.strip(), "0", self._result_detail("archive canary readback", waited))
            else:
                self.assertNotEqual(waited.stdout.strip(), "0", self._result_detail("archive canary readback", waited))
            return waited
        finally:
            identity_check = run_test_process(
                (docker, "inspect", "--format", '{{ index .Config.Labels "sandbox.secret-canary" }}',
                 identity), env=self._env(docker), timeout=15,
                capture_output=True, text=True)
            if identity_check.returncode != 0 or identity_check.stdout.strip() != label.split("=", 1)[1]:
                self.fail("archive canary cleanup identity is unproven")
            removed = run_test_process((docker, "rm", "--force", identity),
                                       env=self._env(docker), timeout=15,
                                       capture_output=True, text=True)
            remaining = run_test_process(
                (docker, "ps", "--all", "--quiet", "--filter", f"label={label}"),
                env=self._env(docker), timeout=15, capture_output=True, text=True)
            if removed.returncode != 0 or remaining.returncode != 0 or remaining.stdout.strip():
                self.fail("archive canary cleanup is unproven")

    def test_archive_backed_secret_is_prepared_before_start(self):
        if self._config().source not in {"archive", "both"}:
            self.skipTest("archive-backed source was not selected")
        docker = self._docker()
        image = self._image(docker)
        expected = hashlib.sha256(_CANARY_VALUE.encode()).hexdigest()
        app_uid = self._config().uid
        app_gid = self._config().gid
        self._archive_secret(
            docker, image=image, uid=app_uid, gid=app_gid,
            secret_uid=app_uid, secret_gid=app_gid, expected=expected,
            container_name=f"sandbox-secret-archive-{uuid.uuid4().hex}")
        mismatch_uid = 65532 if app_uid != 65532 else 65533
        mismatch = None
        try:
            mismatch = self._archive_secret(
                docker, image=image, uid=mismatch_uid, gid=app_gid,
                secret_uid=app_uid, secret_gid=app_gid, expected=expected,
                container_name=f"sandbox-secret-archive-mismatch-{uuid.uuid4().hex}",
                expect_success=False)
        except AssertionError:
            raise
        self.assertIsNotNone(mismatch)

    def test_environment_backed_secret_honors_uid_gid_mode(self):
        if self._config().source not in {"environment", "both"}:
            self.skipTest("environment-backed source was not selected")
        docker = self._docker()
        image = self._image(docker)
        expected = hashlib.sha256(_CANARY_VALUE.encode()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="sandbox-secret-compose-canary-") as directory:
            root = Path(directory).resolve()
            env_file = root / "secret.env"
            env_file.write_text(f"SANDBOX_CANARY_TOKEN={_CANARY_VALUE}\n")
            env_file.chmod(0o600)
            project = f"sandbox-secret-env-{uuid.uuid4().hex}"
            result = self._compose_secret(
                docker, image=image, root=root, env_file=env_file,
                run_uid=self._config().uid, run_gid=self._config().gid,
                secret_uid=self._config().uid, secret_gid=self._config().gid,
                project=project, expected=expected)
            self.assertEqual(result.returncode, 0, self._result_detail(
                "environment-backed secret did not honor uid/gid/mode", result))

            mismatch_uid = 65532 if self._config().uid != 65532 else 65533
            mismatch = self._compose_secret(
                docker, image=image, root=root, env_file=env_file,
                run_uid=mismatch_uid, run_gid=self._config().gid,
                secret_uid=self._config().uid, secret_gid=self._config().gid,
                project=f"{project}-mismatch", expected=expected)
            self.assertNotEqual(mismatch.returncode, 0, self._result_detail(
                "a mismatched UID unexpectedly read the environment secret", mismatch))

    def test_declared_application_uid_can_read_and_other_uid_cannot(self):
        if self._config().source not in {"file", "both"}:
            self.skipTest("file-backed source was not selected")
        docker = self._docker()
        image = self._image(docker)
        app_uid = self._config().uid
        app_gid = self._config().gid
        from sandbox.hosting.images.activation.private_inputs import (
            materialize_secrets,
            publish_candidate,
        )

        canary = b"synthetic-hosted-secret-canary"
        compose = {
            "services": {"web": {"secrets": [{"source": "token", "target": "token"}]}},
            "secrets": {"token": {"environment": "TOKEN"}},
        }
        with tempfile.TemporaryDirectory(prefix="sandbox-secret-canary-") as directory:
            runtime = Path(directory).resolve()
            candidate_id = uuid.uuid4().hex * 2
            candidate = runtime / "activation-inputs" / candidate_id
            effective, files, _binding = materialize_secrets(
                compose, {"TOKEN": canary.decode()}, str(candidate), b"k" * 32
            )
            self.assertEqual(effective["secrets"]["token"]["file"], str(candidate / "secret-0"))
            self.assertEqual(publish_candidate(runtime, candidate_id, files), "prepared")
            secret = candidate / "secret-0"
            self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
            expected = hashlib.sha256(canary).hexdigest()

            app = self._read_digest(
                docker, image, secret, app_uid, app_gid,
                f"sandbox-secret-canary-{uuid.uuid4().hex}", expected,
            )
            self.assertEqual(app.returncode, 0,
                             "declared application UID cannot read the candidate secret")

            mismatch_uid = 65532 if app_uid != 65532 else 65533
            mismatch = self._read_digest(
                docker, image, secret, mismatch_uid, app_gid,
                f"sandbox-secret-canary-{uuid.uuid4().hex}", expected,
            )
            self.assertNotEqual(mismatch.returncode, 0,
                                "a mismatched UID unexpectedly read the candidate secret")


def sys_platform_linux() -> bool:
    # Keep the platform check local so this acceptance module has no hidden
    # import-time skip or subprocess side effect.
    import sys
    return sys.platform.startswith("linux")


if __name__ == "__main__":  # pragma: no cover - explicit acceptance entrypoint
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True,
                        help="already-present immutable image digest")
    parser.add_argument("--uid", required=True, type=int,
                        help="observed non-root application UID")
    parser.add_argument("--gid", required=True, type=int,
                        help="observed non-root application GID")
    parser.add_argument("--source", choices=("file", "environment", "archive", "both"),
                        default="file", help="secret source canary to run")
    parsed, unittest_argv = parser.parse_known_args()
    if (not _IMAGE.fullmatch(parsed.image)
            or not 1 <= parsed.uid <= _MAX_UID
            or not 1 <= parsed.gid <= _MAX_UID):
        parser.error("image must be an immutable digest and UID/GID must be 1..65534")
    HostedSecretAccessCanaryTests.config = CanaryConfig(
        image=parsed.image, uid=parsed.uid, gid=parsed.gid, source=parsed.source)
    unittest.main(argv=[__file__, *unittest_argv])
