from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.core import _remote
from sandbox.resources.network_capacity import evaluate_network_capacity


def evidence(*, total=8, allocated=2, sandbox=1, foreign=1, unattributed=0,
             status="complete"):
    return {
        "status": status,
        "pools": [{
            "pool_id": "pool-" + "a" * 20,
            "capacity_subnets": total,
            "allocated_subnets": allocated,
            "usable_subnets": total - allocated,
        }],
        "totals": {
            "total_subnets": total,
            "allocated_subnets": allocated,
            "usable_subnets": total - allocated,
        },
        "ownership": {
            "sandbox_allocated_subnets": sandbox,
            "foreign_allocated_subnets": foreign,
            "unattributed_allocated_subnets": unattributed,
        },
    }


class NetworkCapacityAdmissionTests(unittest.TestCase):
    def test_sufficient_explicit_capacity_is_admitted(self):
        result = evaluate_network_capacity(evidence(), required_subnets=2,
                                            remote_name="vps")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "admitted")
        self.assertEqual(result["capacity"]["usable_subnets"], 6)
        self.assertEqual(result["resource_class"],
                         "docker_user_defined_network_subnet")
        self.assertEqual(
            result["recovery"]["next_command"],
            "./sb remote docker-pool vps --json",
        )

    def test_exhaustion_blocks_even_when_network_count_would_look_small(self):
        result = evaluate_network_capacity(
            evidence(total=1, allocated=1, sandbox=0, foreign=1),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "docker_network_subnet_exhausted")
        self.assertEqual(result["capacity"]["usable_subnets"], 0)
        self.assertFalse(result["recovery"]["automatic_cleanup"])
        self.assertNotIn("docker network rm", json.dumps(result))

    def test_partial_and_unavailable_fail_closed(self):
        for state in ("partial", "unavailable", "not-a-state"):
            with self.subTest(state=state):
                result = evaluate_network_capacity({"status": state})
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "blocked")
                self.assertIsNone(result["capacity"]["usable_subnets"])

    def test_foreign_and_unattributed_allocations_are_not_claimed_usable(self):
        result = evaluate_network_capacity(
            evidence(total=10, allocated=4, sandbox=1, foreign=2,
                     unattributed=1),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["capacity"]["usable_subnets"], 6)
        self.assertEqual(result["evidence"]["ownership"]["foreign_allocated_subnets"], 2)
        self.assertEqual(result["evidence"]["ownership"]["unattributed_allocated_subnets"], 1)

    def test_probe_unavailable_is_structured_and_does_not_forward_output(self):
        with patch.object(_remote, "ssh_run", return_value=SimpleNamespace(
                returncode=1, stdout="password=secret\n", stderr="secret")):
            result = _remote.remote_network_capacity_admission(
                {"ssh": "untrusted"}, remote_name="vps",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "docker_network_capacity_unavailable")
        self.assertNotIn("secret", json.dumps(result))

    def test_generated_probe_accounts_pool_ipam_and_owner_classes(self):
        # Execute the exact bounded probe source against a fixture daemon
        # config and a fake Docker CLI.  This verifies the probe's pool/IPAM
        # accounting rather than only compiling the embedded source.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "daemon.json"
            config.write_text(json.dumps({
                "default-address-pools": [{
                    "base": "10.250.0.0/28", "size": 30,
                }],
            }))
            docker = root / "docker"
            docker.write_text("""#!/usr/bin/env python3
import json
import sys
if sys.argv[1:] == ["network", "ls", "-q"]:
    print("sandbox-id\\nforeign-id\\nunattributed-id")
elif sys.argv[1:3] == ["network", "inspect"]:
    print(json.dumps([
        {"Id": "sandbox-id", "Name": "sandbox-project_default",
         "Labels": {"com.docker.compose.project": "sandbox-project"},
         "IPAM": {"Config": [{"Subnet": "10.250.0.0/30"}]}},
        {"Id": "foreign-id", "Name": "customer-net",
         "Labels": {"com.docker.compose.project": "customer"},
         "IPAM": {"Config": [{"Subnet": "10.250.0.4/30"}]}},
        {"Id": "unattributed-id", "Name": "unowned-net",
         "Labels": {},
         "IPAM": {"Config": [{"Subnet": "10.250.0.8/30"}]}},
    ]))
else:
    raise SystemExit(2)
""")
            docker.chmod(0o755)
            source = _remote._REMOTE_NETWORK_CAPACITY_PROGRAM.replace(
                'Path("/etc/docker/daemon.json")',
                f"Path({str(config)!r})",
            )
            env = dict(os.environ)
            env["PATH"] = str(root) + os.pathsep + env.get("PATH", "")
            process = subprocess.run(
                [sys.executable, "-c", source],
                capture_output=True, text=True, env=env, check=False,
            )
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["totals"], {
            "total_subnets": 4, "allocated_subnets": 3,
            "usable_subnets": 1,
        })
        self.assertEqual(payload["ownership"], {
            "sandbox_allocated_subnets": 1,
            "foreign_allocated_subnets": 1,
            "unattributed_allocated_subnets": 1,
        })

    def test_probe_reported_reason_is_restricted_to_safe_identifier(self):
        result = evaluate_network_capacity({
            "status": "partial", "reason": "password=secret",
        })
        self.assertNotIn("password", json.dumps(result))
        self.assertEqual(result["evidence"]["reason"], "probe_incomplete")

    def test_deploy_refuses_before_deploy_side_effect_seam(self):
        blocked = evaluate_network_capacity({"status": "partial"})
        with patch.object(_remote, "remote_network_capacity_admission",
                          return_value=blocked), \
             patch.object(_remote, "ensure_deploy_repo") as ensure:
            with self.assertRaises(_remote.NetworkCapacityAdmissionError) as caught:
                _remote.deploy_exact_working_tree({}, "/untrusted/project")
        ensure.assert_not_called()
        self.assertEqual(caught.exception.decision["code"],
                         "docker_network_capacity_unavailable")

    def test_remote_record_carries_safe_name_only_for_admission_guidance(self):
        with patch.object(_remote, "_remote_block",
                          return_value={"vps": {"ssh": "host"}}):
            record = _remote.get_remote("vps")
        self.assertEqual(record["_remote_name"], "vps")
        self.assertNotIn("_remote_name", json.dumps({"ssh": "host"}))
