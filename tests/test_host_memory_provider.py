from __future__ import annotations
import unittest
from unittest.mock import patch
from sandbox.resources.host_memory.provider import RECEIPT, HostProvider
from sandbox.resources.host_memory.models import canonical_digest
from tests.host_memory_assertions import assert_privacy_bounded
from tests.host_memory_fixtures import (
    CGROUP_V1, CGROUP_V2, PROC_MEMINFO, PROC_SWAPS_EMPTY, TARGET,
    command_result, ownership_receipt,
)
import json

class Usage:
    total=100*1024**3; free=80*1024**3

class HostMemoryProviderTest(unittest.TestCase):
    def test_non_linux_is_explicit_and_read_only(self):
        provider=HostProvider(read_text=lambda _p: self.fail("must not read"))
        with patch("platform.system",return_value="Darwin"):
            self.assertEqual(provider.observe()["evidence_state"],"unsupported")
    def test_linux_observation_is_aggregate_only(self):
        def read(path):
            if str(path)=="/proc/meminfo": return "MemTotal: 16777216 kB\nMemAvailable: 12582912 kB\n"
            if str(path)=="/proc/swaps": return "Filename Type Size Used Priority\n"
            if str(path)=="/proc/sys/vm/swappiness": return "60\n"
            raise OSError
        with patch("platform.system",return_value="Linux"), patch("shutil.disk_usage",return_value=Usage()):
            result=HostProvider(read_text=read).observe()
        self.assertEqual(result["evidence_state"], "partial")
        self.assertEqual(result["container_eligibility"]["state"], "unknown")
        self.assertNotIn("processes", result)

    def test_cgroup_v2_limits_are_separate_from_host_swap(self):
        reads = {"/proc/meminfo": PROC_MEMINFO, "/proc/swaps": PROC_SWAPS_EMPTY,
                 "/proc/sys/vm/swappiness": "60\n", **CGROUP_V2}
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=Usage()):
            result = HostProvider(read_text=lambda path: reads[str(path)],
                                  target_identity=TARGET).observe()
        self.assertEqual(result["container_eligibility"]["state"], "limited")
        self.assertEqual(result["container_eligibility"]["memory_limit_bytes"], 12 * 1024 ** 3)
        self.assertEqual(result["container_eligibility"]["swap_limit_bytes"], 2 * 1024 ** 3)
        self.assertEqual(result["swap_areas"], [])
        assert_privacy_bounded(self, result, maximum=256 * 1024)

    def test_cgroup_v1_memsw_is_normalized_to_swap_limit(self):
        reads = {"/proc/meminfo": PROC_MEMINFO, "/proc/swaps": PROC_SWAPS_EMPTY,
                 "/proc/sys/vm/swappiness": "60\n", **CGROUP_V1}
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=Usage()):
            result = HostProvider(read_text=lambda path: reads[str(path)],
                                  target_identity=TARGET).observe()
        self.assertEqual(result["container_eligibility"]["state"], "limited")
        self.assertEqual(result["container_eligibility"]["swap_limit_bytes"], 2 * 1024 ** 3)

    def test_owned_receipt_and_monitor_state_are_observed_without_mutation(self):
        receipt = ownership_receipt()
        reads = {"/proc/meminfo": PROC_MEMINFO, "/proc/swaps": PROC_SWAPS_EMPTY,
                 "/proc/sys/vm/swappiness": "15\n", str(RECEIPT): json.dumps(receipt),
                 "/proc/self/cgroup": "0::/\n", "/sys/fs/cgroup/memory.max": "max",
                 "/sys/fs/cgroup/memory.current": "0",
                 "/sys/fs/cgroup/memory.swap.max": "max",
                 "/sys/fs/cgroup/memory.swap.current": "0"}
        calls = []
        def run(argv, timeout=5):
            calls.append(tuple(argv)); return command_result(output="active")
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=Usage()):
            result = HostProvider(read_text=lambda path: reads[str(path)], run=run,
                                  target_identity=TARGET).observe()
        self.assertEqual(result["target_identity"], TARGET)
        self.assertEqual(result["ownership"], "owned")
        self.assertEqual(result["swappiness"]["effective"], 15)
        self.assertEqual(result["monitor"]["service_state"], "active")
        self.assertTrue(calls)
        self.assertTrue(all(call[0] == "systemctl" for call in calls))

    def test_malformed_proc_data_is_partial_not_zero_filled(self):
        reads = {"/proc/meminfo": "MemTotal: invalid\n", "/proc/swaps": "bad\n",
                 "/proc/sys/vm/swappiness": "bad\n", "/proc/self/cgroup": "bad\n"}
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=Usage()):
            result = HostProvider(read_text=lambda path: reads[str(path)]).observe()
        self.assertEqual(result["evidence_state"], "partial")
        self.assertIsNone(result["memory"].get("total_bytes"))

    def test_active_swap_is_opaque_and_unmanaged_without_matching_receipt(self):
        swap = ("Filename Type Size Used Priority\n"
                "/private/location file 4194304 524288 -2\n")
        reads = {"/proc/meminfo": PROC_MEMINFO, "/proc/swaps": swap,
                 "/proc/sys/vm/swappiness": "15\n", "/proc/self/cgroup": "0::/\n",
                 "/sys/fs/cgroup/memory.max": "max", "/sys/fs/cgroup/memory.current": "0",
                 "/sys/fs/cgroup/memory.swap.max": "max",
                 "/sys/fs/cgroup/memory.swap.current": "0"}
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=Usage()):
            result = HostProvider(read_text=lambda path: reads[str(path)]).observe()
        self.assertEqual(result["evidence_state"], "unmanaged")
        self.assertEqual(result["swap_areas"][0]["ownership"], "unmanaged")
        self.assertNotIn("location", json.dumps(result))

    def test_matching_receipt_owns_active_swap_without_returning_locator(self):
        area_id = canonical_digest({"target_identity":TARGET, "logical_id":"swap_file",
                                    "type":"file", "total_bytes":4*1024**3,
                                    "priority":-2})[:24]
        receipt = ownership_receipt(); receipt["swap_area_id"] = area_id
        swap = ("Filename Type Size Used Priority\n"
                "/var/lib/sandbox/host-memory/sandbox.swap file 4194304 524288 -2\n")
        reads = {"/proc/meminfo": PROC_MEMINFO, "/proc/swaps": swap,
                 "/proc/sys/vm/swappiness": "15\n", str(RECEIPT):json.dumps(receipt),
                 "/proc/self/cgroup":"0::/\n", "/sys/fs/cgroup/memory.max":"max",
                 "/sys/fs/cgroup/memory.current":"0", "/sys/fs/cgroup/memory.swap.max":"max",
                 "/sys/fs/cgroup/memory.swap.current":"0"}
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=Usage()):
            result = HostProvider(read_text=lambda path: reads[str(path)],
                                  run=lambda *a, **k: command_result(),
                                  target_identity=TARGET).observe()
        self.assertEqual(result["swap_areas"][0]["ownership"], "owned")
        self.assertTrue(result["swap_areas"][0]["persistent"])
        assert_privacy_bounded(self, result, maximum=256*1024)

    def test_foreign_same_shape_swap_is_not_owned_by_receipt(self):
        area_id = canonical_digest({"target_identity":TARGET, "logical_id":"swap_file",
                                    "type":"file", "total_bytes":4*1024**3,
                                    "priority":-2})[:24]
        receipt=ownership_receipt(); receipt["swap_area_id"]=area_id
        reads={"/proc/meminfo":PROC_MEMINFO,
               "/proc/swaps":("Filename Type Size Used Priority\n"
                              "/foreign/same-shape.swap file 4194304 524288 -2\n"),
               "/proc/sys/vm/swappiness":"15\n",str(RECEIPT):json.dumps(receipt),**CGROUP_V2}
        with patch("platform.system",return_value="Linux"), \
             patch("shutil.disk_usage",return_value=Usage()):
            result=HostProvider(read_text=lambda path:reads[str(path)],
                                target_identity=TARGET).observe()
        self.assertEqual(result["swap_areas"][0]["ownership"],"unmanaged")
        self.assertNotIn("foreign",json.dumps(result))

    def test_receipt_for_another_target_cannot_own_fixed_swap(self):
        receipt=ownership_receipt(); receipt["target_identity"]="other-host"
        reads={"/proc/meminfo":PROC_MEMINFO,"/proc/swaps":(
               "Filename Type Size Used Priority\n"
               "/var/lib/sandbox/host-memory/sandbox.swap file 4194304 524288 -2\n"),
               "/proc/sys/vm/swappiness":"15\n",str(RECEIPT):json.dumps(receipt),**CGROUP_V2}
        with patch("platform.system",return_value="Linux"), \
             patch("shutil.disk_usage",return_value=Usage()):
            result=HostProvider(read_text=lambda path:reads[str(path)],
                                target_identity=TARGET).observe()
        self.assertNotEqual(result["ownership"],"owned")
        self.assertEqual(result["swap_areas"][0]["ownership"],"unmanaged")

    def test_cgroup_v2_uses_most_restrictive_parent_limit(self):
        reads={"/proc/meminfo":PROC_MEMINFO,"/proc/swaps":PROC_SWAPS_EMPTY,
               "/proc/sys/vm/swappiness":"60\n","/proc/self/cgroup":"0::/parent/leaf\n",
               "/sys/fs/cgroup/parent/leaf/memory.max":str(12*1024**3),
               "/sys/fs/cgroup/parent/leaf/memory.current":str(6*1024**3),
               "/sys/fs/cgroup/parent/leaf/memory.swap.max":str(4*1024**3),
               "/sys/fs/cgroup/parent/leaf/memory.swap.current":"0",
               "/sys/fs/cgroup/parent/memory.max":str(8*1024**3),
               "/sys/fs/cgroup/parent/memory.swap.max":str(2*1024**3),
               "/sys/fs/cgroup/memory.max":"max","/sys/fs/cgroup/memory.swap.max":"max"}
        with patch("platform.system",return_value="Linux"),patch("shutil.disk_usage",return_value=Usage()):
            result=HostProvider(read_text=lambda path:reads[str(path)],target_identity=TARGET).observe()
        self.assertEqual(result["container_eligibility"]["memory_limit_bytes"],8*1024**3)
        self.assertEqual(result["container_eligibility"]["swap_limit_bytes"],2*1024**3)

    def test_missing_cgroup_leaf_or_parent_is_partial_not_root_eligible(self):
        for version, reads in (
            ("v2",{"/proc/self/cgroup":"0::/parent/leaf\n",
                   "/sys/fs/cgroup/memory.max":"max","/sys/fs/cgroup/memory.swap.max":"max"}),
            ("v1",{"/proc/self/cgroup":"5:memory:/parent/leaf\n",
                   "/sys/fs/cgroup/memory/memory.limit_in_bytes":str(16*1024**3),
                   "/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes":str(20*1024**3)}),
        ):
            values={"/proc/meminfo":PROC_MEMINFO,"/proc/swaps":PROC_SWAPS_EMPTY,
                    "/proc/sys/vm/swappiness":"60\n",**reads}
            with self.subTest(version=version),patch("platform.system",return_value="Linux"), \
                 patch("shutil.disk_usage",return_value=Usage()):
                result=HostProvider(read_text=lambda path:values[str(path)],target_identity=TARGET).observe()
            self.assertEqual(result["container_eligibility"]["state"],"unknown")
            self.assertEqual(result["evidence_state"],"partial")
