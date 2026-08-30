from __future__ import annotations

import json
from pathlib import Path
import os
import subprocess
from tests.subprocess_support import synthetic_environment
import sys
import tempfile
import unittest

from sandbox.core import _remote


class RemoteDockerPoolCapacityTests(unittest.TestCase):
    def test_plan_reports_measured_ipam_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text(
                """#!/usr/bin/env python3
import json
import sys
args = sys.argv[1:]
if args[:2] == ["ps", "-q"]:
    pass
elif args[:2] == ["network", "ls"]:
    print("network-one")
elif args[:2] == ["network", "inspect"]:
    print(json.dumps([{"Id": "network-one", "Name": "sandbox-net",
        "Options": {}, "IPAM": {"Config": [{"Subnet": "172.16.0.0/24"}]}}]))
else:
    raise SystemExit(2)
"""
            )
            docker.chmod(0o755)
            ip = binary / "ip"
            ip.write_text("#!/usr/bin/env python3\nprint('[]')\n")
            ip.chmod(0o755)
            config = root / "daemon.json"
            config.write_text(json.dumps({
                "default-address-pools": list(_remote.REMOTE_DOCKER_ADDRESS_POOLS),
            }))
            source = _remote._remote_docker_pool_program(confirm=False)
            source = source.replace(
                'pathlib.Path("/etc/docker/daemon.json")',
                f"pathlib.Path({str(config)!r})",
            ).replace(
                'pathlib.Path("/run/lock/sandbox-docker-pool.lock")',
                f"pathlib.Path({str(root / 'pool.lock')!r})",
            )
            environment = synthetic_environment()
            environment["PATH"] = str(binary) + os.pathsep + environment.get("PATH", "")
            result = subprocess.run(
                [sys.executable, "-c", source],
                capture_output=True, text=True, env=environment, check=False,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout.strip())
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(payload["subnet_capacity_status"], "complete")
            self.assertEqual(payload["subnet_capacity_total"], 4608)
            self.assertEqual(payload["subnet_capacity_allocated"], 1)
            self.assertEqual(payload["subnet_capacity"], 4607)


if __name__ == "__main__":
    unittest.main()
