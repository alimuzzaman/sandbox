from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from tests.subprocess_support import synthetic_environment
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
        self.assertFalse(result["recovery"]["automatic_retry"])
        self.assertFalse(result["retryable"])
        self.assertNotIn("docker network rm", json.dumps(result))

    def test_collision_is_ambiguous_and_has_stable_bounded_error(self):
        collision = {
            **evidence(total=4, allocated=1, sandbox=1),
            "collisions": [
                {"pool_id": "/etc/docker/daemon.json", "network_ids": [
                    "customer-net", "sandbox-net",
                ]},
            ],
        }
        first = evaluate_network_capacity(collision, remote_name="vps")
        second = evaluate_network_capacity(collision, remote_name="vps")
        self.assertEqual(first, second)
        self.assertFalse(first["ok"])
        self.assertEqual(first["code"], "network_allocation_conflict")
        self.assertEqual(first["capacity"]["status"], "partial")
        self.assertEqual(first["evidence"]["collision_count"], 1)
        self.assertFalse(first["recovery"]["automatic_cleanup"])
        self.assertFalse(first["recovery"]["automatic_retry"])
        self.assertFalse(first["retryable"])
        rendered = json.dumps(first)
        self.assertNotIn("daemon.json", rendered)
        self.assertNotIn("customer-net", rendered)
        self.assertNotIn("docker network rm", rendered)

    def test_ambiguous_or_inconsistent_pool_evidence_fails_closed(self):
        duplicate_pool = evidence(total=8, allocated=2, sandbox=1, foreign=1)
        duplicate_pool["pools"].append(dict(duplicate_pool["pools"][0]))
        for candidate, reason in (
            (duplicate_pool, "ambiguous_pool_evidence"),
            ({**evidence(), "totals": {
                "total_subnets": 9, "allocated_subnets": 2, "usable_subnets": 7,
            }}, "inconsistent_pool_totals"),
            ({"status": "complete", "pools": [], "totals": {
                "total_subnets": 0, "allocated_subnets": 0, "usable_subnets": 0,
            }}, "missing_pool_evidence"),
        ):
            with self.subTest(reason=reason):
                result = evaluate_network_capacity(candidate)
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], "docker_network_capacity_unavailable")
                self.assertEqual(result["evidence"]["reason"], reason)
                self.assertIsNone(result["capacity"]["usable_subnets"])

    def test_exhaustion_recovers_only_after_explicit_fresh_complete_evidence(self):
        exhausted = evaluate_network_capacity(
            evidence(total=1, allocated=1, sandbox=1, foreign=0),
        )
        self.assertEqual(exhausted["code"], "docker_network_subnet_exhausted")
        recovered = evaluate_network_capacity(
            evidence(total=2, allocated=1, sandbox=1, foreign=0),
        )
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "admitted")
        self.assertEqual(recovered["capacity"]["usable_subnets"], 1)

    def test_disk_or_network_count_values_never_substitute_for_pool_capacity(self):
        result = evaluate_network_capacity({
            "status": "complete",
            "available_bytes": 10 ** 15,
            "network_count": 0,
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "docker_network_capacity_unavailable")
        self.assertEqual(result["evidence"]["reason"], "probe_incomplete")

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

    def test_remote_admission_uses_one_bounded_probe_without_retry_or_cleanup(self):
        payload = json.dumps({
            "ok": True,
            "status": "partial",
            "reason": "network_allocation_conflict",
            "collision_count": 1,
        })
        with patch.object(_remote, "ssh_run", return_value=SimpleNamespace(
                returncode=0, stdout=payload + "\n", stderr="secret=do-not-forward")) as probe:
            result = _remote.remote_network_capacity_admission(
                {"ssh": "untrusted"}, remote_name="vps", timeout=17,
            )
        probe.assert_called_once()
        self.assertEqual(probe.call_args.kwargs["timeout"], 17)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "network_allocation_conflict")
        self.assertFalse(result["recovery"]["automatic_cleanup"])
        self.assertFalse(result["recovery"]["automatic_retry"])
        self.assertFalse(result["retryable"])
        self.assertNotIn("do-not-forward", json.dumps(result))
        self.assertNotIn("docker network rm", json.dumps(result))

    def test_remote_probe_rejects_unbounded_timeout_before_any_call(self):
        with patch.object(_remote, "ssh_run") as probe:
            for timeout in (0, 301, True):
                with self.subTest(timeout=timeout):
                    with self.assertRaises(ValueError):
                        _remote.remote_network_capacity_admission(
                            {"ssh": "host"}, timeout=timeout,
                        )
        probe.assert_not_called()

    def test_remote_ambiguous_probe_output_fails_closed_without_second_call(self):
        output = "{}\n{" + '"status":"partial"' + "}\n"
        with patch.object(_remote, "ssh_run", return_value=SimpleNamespace(
                returncode=0, stdout=output, stderr="")) as probe:
            result = _remote.remote_network_capacity_admission({"ssh": "host"})
        probe.assert_called_once()
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "docker_network_capacity_unavailable")
        self.assertEqual(result["evidence"]["reason"], "probe_output_ambiguous")

    def test_remote_admission_recovers_on_a_fresh_complete_probe_only(self):
        collision = json.dumps({
            "ok": True,
            "status": "partial",
            "reason": "network_allocation_conflict",
            "collision_count": 1,
        })
        complete = json.dumps({
            "ok": True,
            **evidence(total=2, allocated=1, sandbox=1, foreign=0),
        })
        with patch.object(_remote, "ssh_run", side_effect=(
                SimpleNamespace(returncode=0, stdout=collision, stderr=""),
                SimpleNamespace(returncode=0, stdout=complete, stderr=""),
        )) as probe:
            blocked = _remote.remote_network_capacity_admission({"ssh": "host"})
            recovered = _remote.remote_network_capacity_admission({"ssh": "host"})
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(blocked["code"], "network_allocation_conflict")
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "admitted")

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
if sys.argv[1:] == ["network", "ls", "--no-trunc", "-q"]:
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
            env = synthetic_environment()
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

    def test_generated_probe_matches_full_network_ids_after_no_trunc(self):
        # Docker's default ``network ls -q`` truncates IDs to 12 characters,
        # while ``network inspect`` returns full IDs.  The probe must request
        # untruncated IDs before comparing the two inventories; otherwise the
        # same healthy inventory is reported as ambiguous and admission is
        # blocked.
        full_ids = ["a" * 64, "b" * 64, "c" * 64]
        legacy_ids = [value[:12] for value in full_ids]
        rows = [
            {
                "Id": full_ids[0],
                "Name": "sandbox-project_default",
                "Labels": {"com.docker.compose.project": "sandbox-project"},
                "IPAM": {"Config": [{"Subnet": "10.250.0.0/30"}]},
            },
            {
                "Id": full_ids[1],
                "Name": "customer-network",
                "Labels": {"com.docker.compose.project": "customer"},
                "IPAM": {"Config": [{"Subnet": "10.250.0.4/30"}]},
            },
            {
                "Id": full_ids[2],
                "Name": "unowned-network",
                "Labels": {},
                "IPAM": {"Config": [{"Subnet": "10.250.0.8/30"}]},
            },
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "daemon.json"
            config.write_text(json.dumps({
                "default-address-pools": [{
                    "base": "10.250.0.0/28", "size": 30,
                }],
            }))
            inspect_args = root / "inspect-args.json"
            docker = root / "docker"
            docker.write_text(f"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

full_ids = {full_ids!r}
legacy_ids = {legacy_ids!r}
rows = {rows!r}
if sys.argv[1:] == ["network", "ls", "-q"]:
    print("\\n".join(legacy_ids))
elif sys.argv[1:] == ["network", "ls", "--no-trunc", "-q"]:
    print("\\n".join(full_ids))
elif sys.argv[1:3] == ["network", "inspect"]:
    Path(os.environ["INSPECT_ARGS"]).write_text(json.dumps(sys.argv[3:]))
    print(json.dumps(rows))
else:
    raise SystemExit(2)
""")
            docker.chmod(0o755)
            source = _remote._REMOTE_NETWORK_CAPACITY_PROGRAM.replace(
                'Path("/etc/docker/daemon.json")',
                f"Path({str(config)!r})",
            )
            env = synthetic_environment()
            env["PATH"] = str(root) + os.pathsep + env.get("PATH", "")
            env["INSPECT_ARGS"] = str(inspect_args)

            process = subprocess.run(
                [sys.executable, "-c", source],
                capture_output=True, text=True, env=env, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            payload = json.loads(process.stdout.strip().splitlines()[-1])
            self.assertEqual(json.loads(inspect_args.read_text()), full_ids)
            self.assertEqual(payload["status"], "complete")
            decision = evaluate_network_capacity(payload)
            self.assertTrue(decision["ok"])
            self.assertEqual(decision["status"], "admitted")

            # Re-run the same fixture with the pre-fix command.  The fake
            # daemon returns prefixes for that command, so the old probe sees
            # a set mismatch against inspect's full IDs and fails closed.
            legacy_source = source.replace(
                '["docker", "network", "ls", "--no-trunc", "-q"]',
                '["docker", "network", "ls", "-q"]',
            )
            self.assertNotEqual(source, legacy_source)
            process = subprocess.run(
                [sys.executable, "-c", legacy_source],
                capture_output=True, text=True, env=env, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            legacy_payload = json.loads(process.stdout.strip().splitlines()[-1])
            self.assertEqual(json.loads(inspect_args.read_text()), legacy_ids)
            self.assertEqual(legacy_payload, {
                "ok": True,
                "status": "partial",
                "reason": "network_inventory_ambiguous",
            })
            legacy_decision = evaluate_network_capacity(legacy_payload)
            self.assertFalse(legacy_decision["ok"])
            self.assertEqual(
                legacy_decision["evidence"]["reason"],
                "network_inventory_ambiguous",
            )

    def test_generated_probe_reports_pool_collision_without_network_details(self):
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
if sys.argv[1:] == ["network", "ls", "--no-trunc", "-q"]:
    print("first-id\\nsecond-id")
elif sys.argv[1:3] == ["network", "inspect"]:
    print(json.dumps([
        {"Id": "first-id", "Name": "customer-network",
         "Labels": {"com.docker.compose.project": "customer"},
         "IPAM": {"Config": [{"Subnet": "10.250.0.0/30"}]}},
        {"Id": "second-id", "Name": "sandbox-network",
         "Labels": {"com.docker.compose.project": "sandbox-project"},
         "IPAM": {"Config": [{"Subnet": "10.250.0.0/30"}]}},
    ]))
else:
    raise SystemExit(2)
""")
            docker.chmod(0o755)
            source = _remote._REMOTE_NETWORK_CAPACITY_PROGRAM.replace(
                'Path("/etc/docker/daemon.json")',
                f"Path({str(config)!r})",
            )
            env = synthetic_environment()
            env["PATH"] = str(root) + os.pathsep + env.get("PATH", "")
            process = subprocess.run(
                [sys.executable, "-c", source],
                capture_output=True, text=True, env=env, check=False,
            )
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(payload, {
            "ok": True,
            "status": "partial",
            "reason": "network_allocation_conflict",
            "collision_count": 1,
        })
        self.assertNotIn("customer-network", process.stdout)
        self.assertNotIn("sandbox-network", process.stdout)

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


def range_evidence(*, capacity=4, allocated=0, granted=(), exhausted=False, table=()):
    """The admission program's ``range`` result (spec 063 contract)."""
    return {
        "granted": [{"allocation_id": g, "network": "default", "subnet": "10.200.0.0/26"}
                    for g in granted],
        "exhausted": exhausted, "no_range": capacity == 0,
        "range": {"capacity": capacity, "allocated": allocated, "usable": capacity - allocated},
        "table": list(table),
    }


NO_POOLS = {"ok": False, "status": "unavailable", "code": "docker_address_pools_unavailable",
            "reason": "daemon address-pool configuration is unavailable"}
GRANT = "a-" + "1" * 16


class RangeCapacityTests(unittest.TestCase):
    """Spec 063 FR-007..FR-009: pool and range capacity are evaluated together."""

    def test_range_alone_admits_when_no_daemon_pool_is_configured(self):
        result = evaluate_network_capacity(
            NO_POOLS, required_subnets=1, remote_name="vps",
            range_evidence=range_evidence(capacity=4, allocated=1, granted=[GRANT]))
        self.assertTrue(result["ok"])
        self.assertEqual(result["capacity"]["range_usable_subnets"], 3)
        self.assertEqual(result["capacity"]["usable_subnets"], 3)
        self.assertEqual(result["granted"], [GRANT])
        self.assertNotIn("10.200", json.dumps(result))

    def test_pool_and_range_capacity_are_summed(self):
        result = evaluate_network_capacity(
            evidence(total=4, allocated=2), required_subnets=1,
            range_evidence=range_evidence(capacity=4, allocated=1, granted=[GRANT]))
        self.assertTrue(result["ok"])
        self.assertEqual(result["capacity"]["usable_subnets"], 2 + 3)
        self.assertEqual(result["capacity"]["range_usable_subnets"], 3)

    def test_no_pool_and_no_range_is_missing_evidence_naming_the_range_remedy(self):
        for candidate in (NO_POOLS, {"status": "complete", "pools": [], "totals": {
                "total_subnets": 0, "allocated_subnets": 0, "usable_subnets": 0}}):
            for ranged in (None, range_evidence(capacity=0)):
                with self.subTest(candidate=candidate.get("status"), ranged=ranged is not None):
                    result = evaluate_network_capacity(candidate, remote_name="vps",
                                                       range_evidence=ranged)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["code"], "docker_network_capacity_unavailable")
                    self.assertEqual(result["evidence"]["reason"], "missing_pool_evidence")
                    self.assertEqual(result["recovery"]["next_command"],
                                     "./sb remote network-range propose vps")
                    self.assertIn("missing evidence, not exhausted capacity",
                                  result["recovery"]["guidance"])
                    self.assertIsNone(result["capacity"]["usable_subnets"])

    def test_other_probe_failures_stay_fail_closed_despite_a_range(self):
        ranged = range_evidence(capacity=4, granted=[GRANT])
        for candidate, reason in (
            ({"ok": False, "status": "unavailable", "code": "docker_network_inventory_unavailable"},
             "probe_not_successful"),
            ({"ok": True, "status": "partial", "reason": "network_ipam_unavailable"},
             "network_ipam_unavailable"),
        ):
            with self.subTest(reason=reason):
                result = evaluate_network_capacity(candidate, range_evidence=ranged)
                self.assertFalse(result["ok"])
                self.assertEqual(result["evidence"]["reason"], reason)

    def test_default_pools_never_count(self):
        # Without daemon pools Docker would fall back to its built-in pools;
        # the evaluator has no input for them, so only the range counts.
        result = evaluate_network_capacity(
            NO_POOLS, required_subnets=2,
            range_evidence=range_evidence(capacity=1, exhausted=True))
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "docker_network_subnet_exhausted")
        self.assertEqual(result["capacity"]["usable_subnets"], 1)

    def test_range_exhaustion_returns_bounded_table_and_release_commands(self):
        table = [{"allocation_id": f"a-{i:016x}", "owner_id": f"w{i}", "owner_kind": "workspace",
                  "workspace_id": f"w{i}", "age_seconds": 60 * i} for i in range(40)]
        table.append({"owner_id": "x; rm -rf /", "owner_kind": "job",
                      "workspace_id": "$(id)", "age_seconds": 1})
        result = evaluate_network_capacity(
            evidence(total=1, allocated=1, sandbox=1, foreign=0), required_subnets=1,
            remote_name="vps",
            range_evidence=range_evidence(capacity=2, allocated=2, exhausted=True, table=table))
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "docker_network_subnet_exhausted")
        rows = result["allocation_table"]
        self.assertLessEqual(len(rows), 32)
        self.assertEqual(set(rows[0]), {"owner_id", "owner_kind", "workspace_id", "age_seconds"})
        self.assertEqual(result["release_commands"][0], "./sb workspace release w0 --remote vps && ./sb workspace reap --remote vps --confirm")
        self.assertLessEqual(len(result["release_commands"]), 32)
        rendered = json.dumps(result)
        self.assertNotIn("10.200", rendered)
        self.assertNotIn("rm -rf", rendered)
        self.assertNotIn("$(id)", rendered)
        self.assertFalse(result["recovery"]["automatic_cleanup"])

    def test_granted_ids_are_validated_and_partial_grants_refuse(self):
        bad = range_evidence(capacity=4, granted=["../etc"])
        result = evaluate_network_capacity(NO_POOLS, range_evidence=bad)
        self.assertFalse(result["ok"])
        self.assertEqual(result["evidence"]["reason"], "invalid_range_evidence")
        for broken in ({"range": {"capacity": -1, "allocated": 0, "usable": -1}},
                       {"range": {"capacity": 2, "allocated": 3, "usable": 0}}, "nope"):
            with self.subTest(broken=broken):
                result = evaluate_network_capacity(evidence(), range_evidence=broken)
                self.assertFalse(result["ok"])
                self.assertEqual(result["evidence"]["reason"], "invalid_range_evidence")

    def test_without_range_evidence_behaviour_is_unchanged(self):
        self.assertEqual(evaluate_network_capacity(evidence(), remote_name="vps"),
                         evaluate_network_capacity(evidence(), remote_name="vps",
                                                   range_evidence=None))
        self.assertNotIn("range_usable_subnets",
                         evaluate_network_capacity(evidence())["capacity"])


class RangeCapacityHardeningTests(unittest.TestCase):
    """Sol round 4: R4-2 and R4-5 plus grant validation."""

    def test_empty_pools_preserve_other_refusals(self):
        ranged = range_evidence(capacity=4, granted=[GRANT])
        empty = {"status": "complete", "pools": [], "totals": {
            "total_subnets": 0, "allocated_subnets": 0, "usable_subnets": 0}}
        for candidate, code, reason in (
            ({**empty, "ok": False}, "docker_network_capacity_unavailable", "probe_not_successful"),
            ({**empty, "collisions": [{"pool_id": "p"}]}, "network_allocation_conflict",
             "network_allocation_conflict"),
            ({**empty, "totals": {"total_subnets": 5, "allocated_subnets": 0,
                                  "usable_subnets": 5}},
             "docker_network_capacity_unavailable", "inconsistent_pool_totals"),
            ({**empty, "totals": {"total_subnets": 0}},
             "docker_network_capacity_unavailable", "invalid_capacity_totals"),
        ):
            with self.subTest(reason=reason):
                result = evaluate_network_capacity(candidate, range_evidence=ranged)
                self.assertFalse(result["ok"])
                self.assertEqual((result["code"], result["evidence"]["reason"]), (code, reason))
        self.assertTrue(evaluate_network_capacity(empty, range_evidence=ranged)["ok"])

    def test_range_exhaustion_rejects_secret_shaped_ids(self):
        canary = "ghp_" + "A" * 36
        table = [
            {"owner_id": canary, "owner_kind": "job", "workspace_id": "w1", "age_seconds": 1},
            {"owner_id": "j2", "owner_kind": "job", "workspace_id": "sk-" + "a" * 40,
             "age_seconds": 2},
            {"owner_id": "w3", "owner_kind": "workspace", "workspace_id": "w3", "age_seconds": 3},
        ]
        result = evaluate_network_capacity(
            NO_POOLS, remote_name="vps",
            range_evidence=range_evidence(capacity=1, allocated=1, exhausted=True, table=table))
        self.assertEqual(result["code"], "docker_network_subnet_exhausted")
        rendered = json.dumps(result)
        self.assertNotIn("ghp_", rendered)
        self.assertNotIn("sk-", rendered)
        self.assertEqual([row["owner_id"] for row in result["allocation_table"]], ["w3"])
        self.assertEqual(result["release_commands"], ["./sb workspace release w3 --remote vps && ./sb workspace reap --remote vps --confirm"])

    def test_partial_and_duplicate_range_grants_refuse(self):
        other = "a-" + "2" * 16
        for granted, required in (([GRANT], 2), ([GRANT, GRANT], 2), ([GRANT, other], 3)):
            with self.subTest(granted=granted, required=required):
                result = evaluate_network_capacity(
                    NO_POOLS, required_subnets=required,
                    range_evidence=range_evidence(capacity=4, allocated=2, granted=granted))
                self.assertFalse(result["ok"])
                self.assertEqual(result["evidence"]["reason"], "invalid_range_evidence")
        self.assertTrue(evaluate_network_capacity(
            NO_POOLS, required_subnets=2,
            range_evidence=range_evidence(capacity=4, allocated=2, granted=[GRANT, other]))["ok"])


class RangeCapacityRound5Tests(unittest.TestCase):
    def test_partial_missing_pool_reason_preserves_refusal(self):
        """Sol R5-1: a partial probe that merely names the reason is not absence."""
        ranged = range_evidence(capacity=4, granted=[GRANT])
        for candidate in (
            {"ok": True, "status": "partial", "reason": "missing_pool_evidence"},
            {"ok": True, "status": "unavailable", "reason": "missing_pool_evidence"},
            {"status": "complete", "reason": "missing_pool_evidence"},
        ):
            with self.subTest(candidate=candidate):
                result = evaluate_network_capacity(candidate, range_evidence=ranged)
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], "docker_network_capacity_unavailable")

class RangeAdmissionTests(unittest.TestCase):
    """Spec 063 R4 revision: admission counts range capacity, read-only."""

    def setUp(self):
        from tests.test_remote_network_program import HOLDER, NEW, _LocalRemote, _write_tool
        from sandbox.remote_network import store as range_store
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.home, bin_dir = root / "home", root / "bin"
        self.home.mkdir()
        bin_dir.mkdir()
        inspect = json.dumps([{"IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}}])
        _write_tool(bin_dir / "docker", 'case "$2" in ls) echo net1;; inspect) cat <<\'EOF\'\n'
                    + inspect + "\nEOF\n;; esac\n")
        _write_tool(bin_dir / "ip", "cat <<'EOF'\n" + json.dumps([{"dst": "default"}]) + "\nEOF\n")
        self.local = _LocalRemote(self.home, bin_dir)
        self.store = range_store.RangeStore({"name": "vps"}, self.local, installed_protocol=NEW)
        self.holder = HOLDER
        self.commands = []

    def ssh(self, pools):
        def run(entry, command, timeout=30):
            self.commands.append(command)
            if command.startswith("sudo -n "):
                return SimpleNamespace(returncode=0, stdout=json.dumps(pools) + "\n", stderr="")
            return self.local(entry, command, timeout=timeout)
        return run

    def admit(self, pools, required=1):
        with patch.object(_remote, "ssh_run", side_effect=self.ssh(pools)):
            return _remote.remote_network_capacity_admission(
                {"ssh": "host"}, required_subnets=required, remote_name="vps")

    def test_a_range_admits_a_remote_without_daemon_pools(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=self.holder)
        result = self.admit(NO_POOLS, required=2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["capacity"]["range_usable_subnets"], 4)
        self.assertEqual(result.get("granted"), [])
        self.assertNotIn("10.200", json.dumps(result))
        self.assertEqual(len(self.commands), 2)

    def test_ranges_are_not_consulted_for_other_pool_outcomes(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=self.holder)
        self.commands.clear()
        conflict = {"ok": True, "status": "partial", "reason": "network_allocation_conflict",
                    "collision_count": 1}
        result = self.admit(conflict)
        self.assertEqual(result["code"], "network_allocation_conflict")
        self.assertTrue(all(command.startswith("sudo -n ") for command in self.commands))

    def test_without_a_range_the_missing_pool_refusal_is_unchanged(self):
        result = self.admit(NO_POOLS)
        self.assertFalse(result["ok"])
        self.assertEqual(result["evidence"]["reason"], "missing_pool_evidence")
        self.assertFalse((self.home / "runtime" / "network-ranges").exists())

    def test_exhausted_range_refuses_with_the_allocation_table(self):
        from sandbox.remote_network import runtime as range_runtime
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=self.holder)
        self.store.allocate(owner_kind="instance", owner_id="instance:a",
                            workspace_id="site-a", networks=["sandbox-a_default",
                                                             "sandbox-a_backend"])
        result = self.admit(NO_POOLS)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], range_runtime.EXHAUSTED)
        self.assertEqual([row["owner_id"] for row in result["allocation_table"]],
                         ["instance:a", "instance:a"])
        self.assertEqual(result["release_commands"], ["./sb workspace release site-a --remote vps && ./sb workspace reap --remote vps --confirm"])
        self.assertNotIn("10.200", json.dumps(result))

    def test_an_unreachable_range_store_keeps_the_pool_decision(self):
        def run(entry, command, timeout=30):
            if command.startswith("sudo -n "):
                return SimpleNamespace(returncode=0, stdout=json.dumps(NO_POOLS) + "\n", stderr="")
            raise OSError("ssh: token=secret")
        with patch.object(_remote, "ssh_run", side_effect=run):
            result = _remote.remote_network_capacity_admission({"ssh": "host"}, remote_name="vps")
        self.assertEqual(result["evidence"]["reason"], "missing_pool_evidence")
        self.assertNotIn("secret", json.dumps(result))


class RangeAdmissionPayloadTests(unittest.TestCase):
    def test_public_refusal_keeps_the_table_and_release_commands(self):
        from sandbox.transports.remote_jobs import RemoteJobAdmissionError
        decision = {
            "code": "docker_network_subnet_exhausted",
            "allocation_table": [
                {"owner_kind": "instance", "owner_id": "instance:a", "workspace_id": "site-a",
                 "age_seconds": 5, "subnet": "10.200.0.0/26"},
                {"owner_kind": "bogus", "owner_id": "x", "workspace_id": "y", "age_seconds": 1},
                {"owner_kind": "workspace", "owner_id": "$(id)", "workspace_id": "w", "age_seconds": 1},
            ],
            "release_commands": [
                "./sb workspace release site-a --remote vps && ./sb workspace reap --remote vps --confirm",
                "./sb workspace release site-a --remote vps; rm -rf /",
            ],
        }
        payload = RemoteJobAdmissionError(decision).to_payload(remote="vps", operation="exec")
        self.assertEqual(payload["code"], "docker_network_subnet_exhausted")
        self.assertEqual(payload["allocation_table"], [
            {"owner_kind": "instance", "owner_id": "instance:a", "workspace_id": "site-a",
             "age_seconds": 5}])
        self.assertEqual(payload["recovery"]["release_commands"], [decision["release_commands"][0]])
        self.assertNotIn("10.200", json.dumps(payload))

    def test_pool_refusal_has_no_range_fields(self):
        from sandbox.transports.remote_jobs import RemoteJobAdmissionError
        payload = RemoteJobAdmissionError({"code": "docker_network_capacity_unavailable"}).to_payload()
        self.assertNotIn("allocation_table", payload)
        self.assertNotIn("release_commands", payload["recovery"])
