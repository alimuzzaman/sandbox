"""Disposable Linux proof for the fixed, read-only settlement helper."""

import argparse
import base64
import inspect
import json
import subprocess
import uuid

from sandbox.hosting.images.activation import private_settlement, settlement_diagnostics
from sandbox.hosting.images.activation.private_graph import graph_command_port
from tests.subprocess_support import run_test_process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    assert "@sha256:" in args.image
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
    command, _deadline = graph_command_port(env, 120)
    project = "sb-settlement-canary-" + uuid.uuid4().hex
    volume = project + "-data"
    identity = None
    created_volume = False
    try:
        epoch = command(["docker", "info", "--format", "{{.ID}}"], max_output_bytes=4096).decode().strip()
        command(["docker", "volume", "create", "--label", "com.docker.compose.project=" + project, volume])
        created_volume = True
        identity = command(["docker", "create", "--pull", "never", "--network", "none", "--restart", "no",
            "--label", "com.docker.compose.project=" + project, "--label", "com.docker.compose.service=probe",
            "--mount", "type=volume,source=" + volume + ",target=/canary", "--entrypoint", "/bin/sh",
            args.image, "-c", "sleep 90"]).decode().strip()
        assert private_settlement._HEX.fullmatch(identity)
        frame = {"target": {"machine_identity": "disposable-canary", "target_identity": project, "daemon_identity": epoch},
            "compose_project": project, "transaction_digest": "sha256:" + "a" * 64,
            "generation": 0, "binding_key": base64.b64encode(b"k" * 32).decode()}
        # The fixed helper needs metadata visibility across /proc and Docker's
        # volume directories; only its closed output is captured.
        program = (inspect.getsource(settlement_diagnostics) + "\n" + inspect.getsource(private_settlement)
                   + "\n" + inspect.getsource(graph_command_port) + "\nmain()\n")
        def observe():
            result = run_test_process(["sudo", "-n", "python3", "-c", program],
                input=json.dumps(frame).encode(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=55, check=False)
            assert result.returncode == 0 and not result.stderr
            assert len(result.stdout) < 65536
            return json.loads(result.stdout)
        first = observe()
        if first.get("ok") is not True:
            diagnostic = program.rsplit("\nmain()\n", 1)[0] + '''
import sys, traceback
try:
    command, deadline = graph_command_port(_ENV, 45)
    inventory(json.load(sys.stdin), command, deadline=deadline)
except Exception as error:
    print(json.dumps({"error_type": type(error).__name__, "frames": [
        {"function": item.name, "line": item.lineno}
        for item in traceback.extract_tb(error.__traceback__)[-5:]]}))
'''
            detail = run_test_process(["sudo", "-n", "python3", "-c", diagnostic],
                input=json.dumps(frame).encode(), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=55, check=False)
            raise AssertionError({"result": first, "diagnostic": json.loads(detail.stdout)})
        assert first == observe()
        assert first["observation"]["container_identities"] == [identity]
        assert len(first["observation"]["preserved_identities"]) == 2
        command(["docker", "start", identity])
        assert observe() == {"ok": False, "code": "not_quiescent", "diagnostic": {
            "schema_version": 1, "reason": "container_not_stopped", "subject": "owned_container",
            "sample": "first", "container_id": identity}}
        command(["docker", "stop", "--time", "2", identity])
        assert observe().get("ok") is True
        print(json.dumps({"ok": True, "code": "settlement_observer_canary_passed"}))
    finally:
        if identity:
            metadata = json.loads(command(["docker", "inspect", identity]))[0]
            assert metadata["Id"] == identity and metadata["Config"]["Labels"]["com.docker.compose.project"] == project
            if metadata["State"]["Running"]:
                command(["docker", "stop", "--time", "2", identity])
            command(["docker", "rm", identity])
        if created_volume:
            metadata = json.loads(command(["docker", "volume", "inspect", volume]))[0]
            assert metadata["Name"] == volume and metadata["Labels"]["com.docker.compose.project"] == project
            command(["docker", "volume", "rm", volume])


if __name__ == "__main__":
    main()
