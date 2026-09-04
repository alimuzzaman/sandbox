from __future__ import annotations
import unittest
from unittest.mock import patch
from sandbox.resources.host_memory.provider import (
    RECEIPT, STATE, SWAP, SWAP_UNIT, FIXED_ARTIFACTS, HostProvider,
)
from sandbox.resources.host_memory.models import canonical_digest
from tests.host_memory_assertions import assert_privacy_bounded
from tests.host_memory_fixtures import (
    CGROUP_V1, CGROUP_V2, PROC_MEMINFO, PROC_SWAPS_EMPTY, SYSCTL_TEXT,
    NOW, SWAP_UNIT_TEXT, TARGET, command_result, ownership_receipt,
)
import json
import stat as statmod
from pathlib import Path
from types import SimpleNamespace

class Usage:
    total=100*1024**3; free=80*1024**3

def safe_stat(path):
    path = str(path)
    if path == str(STATE):
        return SimpleNamespace(st_mode=statmod.S_IFDIR | 0o700, st_uid=0, st_nlink=2)
    mode = 0o600 if path in {str(RECEIPT), str(SWAP)} else 0o644
    return SimpleNamespace(st_mode=statmod.S_IFREG | mode, st_uid=0, st_nlink=1)

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
                 str(SWAP_UNIT):SWAP_UNIT_TEXT,
                 str(FIXED_ARTIFACTS["swappiness_policy"]):SYSCTL_TEXT,
                 "/proc/self/cgroup": "0::/\n", "/sys/fs/cgroup/memory.max": "max",
                 "/sys/fs/cgroup/memory.current": "0",
                 "/sys/fs/cgroup/memory.swap.max": "max",
                 "/sys/fs/cgroup/memory.swap.current": "0"}
        calls = []
        def run(argv, timeout=5):
            calls.append(tuple(argv)); return command_result(
                output="enabled" if argv[1]=="is-enabled" else "active")
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=Usage()):
            result = HostProvider(read_text=lambda path: reads[str(path)], stat=safe_stat, run=run,
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
                 str(SWAP_UNIT):SWAP_UNIT_TEXT,
                 str(FIXED_ARTIFACTS["swappiness_policy"]):SYSCTL_TEXT,
                 "/proc/self/cgroup":"0::/\n", "/sys/fs/cgroup/memory.max":"max",
                 "/sys/fs/cgroup/memory.current":"0", "/sys/fs/cgroup/memory.swap.max":"max",
                 "/sys/fs/cgroup/memory.swap.current":"0"}
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=Usage()):
            result = HostProvider(read_text=lambda path: reads[str(path)], stat=safe_stat,
                                  run=lambda argv, **k: command_result(
                                      output="enabled" if argv[1]=="is-enabled" else "active"),
                                  target_identity=TARGET).observe()
        self.assertEqual(result["swap_areas"][0]["ownership"], "owned")
        self.assertTrue(result["swap_areas"][0]["persistent"])
        assert_privacy_bounded(self, result, maximum=256*1024)

    def test_receipt_and_persistence_require_safe_fixed_artifact_attestation(self):
        area_id=canonical_digest({"target_identity":TARGET,"logical_id":"swap_file",
            "type":"file","total_bytes":4*1024**3,"priority":-2})[:24]
        receipt=ownership_receipt(); receipt["swap_area_id"]=area_id
        base={"/proc/meminfo":PROC_MEMINFO,"/proc/swaps":(
              "Filename Type Size Used Priority\n"
              f"{SWAP} file 4194304 524288 -2\n"),
              "/proc/sys/vm/swappiness":"15\n",str(RECEIPT):json.dumps(receipt),
              str(SWAP_UNIT):SWAP_UNIT_TEXT,
              str(FIXED_ARTIFACTS["swappiness_policy"]):SYSCTL_TEXT,**CGROUP_V2}
        unsafe_stats=(
            lambda path: SimpleNamespace(st_mode=statmod.S_IFLNK | 0o777,st_uid=0,st_nlink=1)
                if str(path)==str(RECEIPT) else safe_stat(path),
            lambda path: SimpleNamespace(st_mode=statmod.S_IFREG | 0o600,st_uid=0,st_nlink=2)
                if str(path)==str(RECEIPT) else safe_stat(path),
            lambda path: SimpleNamespace(st_mode=statmod.S_IFREG | 0o666,st_uid=0,st_nlink=1)
                if str(path)==str(RECEIPT) else safe_stat(path),
            lambda path: SimpleNamespace(st_mode=statmod.S_IFREG | 0o600,st_uid=501,st_nlink=1)
                if str(path)==str(RECEIPT) else safe_stat(path),
        )
        variants=[(base,stat_fn) for stat_fn in unsafe_stats]
        variants.append(({**base,str(SWAP_UNIT):SWAP_UNIT_TEXT+"foreign"},safe_stat))
        variants.append(({**base,str(FIXED_ARTIFACTS["swappiness_policy"]):"vm.swappiness=60\n"},safe_stat))
        for reads, stat_fn in variants:
            with self.subTest(reads=reads),patch("platform.system",return_value="Linux"), \
                 patch("shutil.disk_usage",return_value=Usage()):
                result=HostProvider(read_text=lambda path:reads[str(path)],stat=stat_fn,
                                    target_identity=TARGET).observe()
            self.assertNotEqual(result["ownership"],"owned")
            self.assertEqual(result["swap_areas"][0]["ownership"],"unmanaged")
            self.assertFalse(result["swap_areas"][0]["persistent"])
            self.assertFalse(result["swappiness"]["owned"])

        with patch("sandbox.resources.host_memory.provider.RECEIPT",
                   Path("/outside/receipt.json")),patch("platform.system",return_value="Linux"), \
             patch("shutil.disk_usage",return_value=Usage()):
            result=HostProvider(read_text=lambda path:base[str(path)],stat=safe_stat,
                                target_identity=TARGET).observe()
        self.assertNotEqual(result["ownership"],"owned")

    def test_proc_swaps_header_and_duplicate_cgroup_locators_fail_closed(self):
        variants=(
            {"/proc/swaps":"Path Type Size Used Priority\n",**CGROUP_V2},
            {"/proc/swaps":PROC_SWAPS_EMPTY,**CGROUP_V2,
             "/proc/meminfo":"MemTotal: 1 MB\nMemAvailable: 1 kB\n"},
            {"/proc/swaps":PROC_SWAPS_EMPTY,**CGROUP_V2,
             "/proc/meminfo":"MemTotal: 1 kB\nMemTotal: 2 kB\nMemAvailable: 1 kB\n"},
            {"/proc/swaps":PROC_SWAPS_EMPTY,**CGROUP_V2,
             "/proc/self/cgroup":"0::/fixture.scope\n0::/other.scope\n"},
            {"/proc/swaps":PROC_SWAPS_EMPTY,**CGROUP_V2,
             "/proc/self/cgroup":"0::/fixture.scope\n5:memory:/fixture.scope\n"},
            {"/proc/swaps":("Filename Type Size Used Priority\n"
                            "/same file 1 0 -2\n/same file 1 0 -2\n"),**CGROUP_V2},
        )
        for extra in variants:
            reads={"/proc/meminfo":PROC_MEMINFO,"/proc/sys/vm/swappiness":"60\n",**extra}
            with self.subTest(extra=extra),patch("platform.system",return_value="Linux"), \
                 patch("shutil.disk_usage",return_value=Usage()):
                result=HostProvider(read_text=lambda path:reads[str(path)],
                                    target_identity=TARGET).observe()
            self.assertNotEqual(result["evidence_state"],"known")

    def test_observation_budget_reaches_systemctl_timeout(self):
        receipt=ownership_receipt()
        reads={"/proc/meminfo":PROC_MEMINFO,"/proc/swaps":PROC_SWAPS_EMPTY,
               "/proc/sys/vm/swappiness":"15\n",str(RECEIPT):json.dumps(receipt),
               str(SWAP_UNIT):SWAP_UNIT_TEXT,
               str(FIXED_ARTIFACTS["swappiness_policy"]):SYSCTL_TEXT,**CGROUP_V2}
        timeouts=[]
        with patch("platform.system",return_value="Linux"),patch("shutil.disk_usage",return_value=Usage()):
            HostProvider(read_text=lambda path:reads[str(path)],stat=safe_stat,
                run=lambda argv,timeout: timeouts.append(timeout) or command_result(),
                target_identity=TARGET).observe(budget_seconds=0.25)
        self.assertTrue(timeouts)
        self.assertTrue(all(0 < value <= 0.25 for value in timeouts))

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

    def test_cgroup_v1_contradictory_memsw_limits_or_usage_are_partial(self):
        for field,value in (
            ("/sys/fs/cgroup/memory/fixture.scope/memory.memsw.limit_in_bytes",11*1024**3),
            ("/sys/fs/cgroup/memory/fixture.scope/memory.memsw.usage_in_bytes",5*1024**3),
        ):
            reads={"/proc/meminfo":PROC_MEMINFO,"/proc/swaps":PROC_SWAPS_EMPTY,
                   "/proc/sys/vm/swappiness":"60\n",**CGROUP_V1,field:str(value)}
            with self.subTest(field=field),patch("platform.system",return_value="Linux"), \
                 patch("shutil.disk_usage",return_value=Usage()):
                result=HostProvider(read_text=lambda path:reads[str(path)],
                                    target_identity=TARGET).observe()
            self.assertEqual(result["container_eligibility"]["state"],"unknown")
            self.assertEqual(result["evidence_state"],"partial")

    def _enable_plan(self, size_gib=4):
        from sandbox.resources.host_memory.policy import build_plan
        from tests.host_memory_fixtures import NOW, eligible_state, service_evidence
        return build_plan("enable", service_evidence(), eligible_state(),
                          size_gib=size_gib, now=NOW)

    def test_enable_uses_only_fixed_paths_with_restrictive_modes(self):
        from sandbox.resources.host_memory.provider import (
            FIXED_ARTIFACTS, RECEIPT, STATE, SWAP, SWAP_UNIT, HostProvider,
        )
        mutated = []
        def run(argv, timeout=5):
            mutated.append(tuple(argv)); return command_result()
        provider = HostProvider(read_text=lambda path: PROC_MEMINFO, stat=safe_stat,
                                run=run, target_identity=TARGET, now=lambda: NOW)
        result = provider.enable(self._enable_plan())
        self.assertEqual(result["status"], "applied")
        allowed = {str(STATE), str(RECEIPT), str(SWAP), str(SWAP_UNIT),
                   *(str(path) for path in FIXED_ARTIFACTS.values())}
        for call in mutated:
            text = " ".join(call)
            self.assertTrue(any(root in text for root in allowed), text)
        self.assertNotIn("/tmp/evil", " ".join(" ".join(call) for call in mutated))

    def test_enable_validates_plan_before_side_effects(self):
        from datetime import timedelta
        from sandbox.resources.host_memory.policy import PolicyRefusal, build_plan
        from tests.host_memory_fixtures import NOW, eligible_state, service_evidence
        calls = []
        provider = HostProvider(read_text=lambda path: PROC_MEMINFO, stat=safe_stat,
                                run=lambda argv, timeout=5: calls.append(tuple(argv)) or command_result(),
                                target_identity=TARGET, now=lambda: NOW)
        stale_time = NOW - timedelta(minutes=16)
        stale = build_plan("enable", service_evidence(),
                           eligible_state(observed_at=stale_time.isoformat().replace("+00:00", "Z")),
                           now=stale_time)
        with self.assertRaises(PolicyRefusal) as refused:
            provider.enable(stale)
        self.assertEqual(refused.exception.code, "plan_expired")
        self.assertEqual(calls, [])

    def test_enable_is_idempotent_for_the_same_plan(self):
        from sandbox.resources.host_memory.provider import HostProvider
        calls = []
        provider = HostProvider(read_text=lambda path: PROC_MEMINFO, stat=safe_stat,
                                run=lambda argv, timeout=5: calls.append(tuple(argv)) or command_result(),
                                target_identity=TARGET, now=lambda: NOW)
        plan = self._enable_plan()
        first = provider.enable(plan)
        calls_after_first = len(calls)
        second = provider.enable(plan)
        self.assertEqual(first["status"], "applied")
        self.assertEqual(second["status"], "already_current")
        self.assertEqual(len(calls), calls_after_first)

    def _preflight_provider(self, calls):
        from sandbox.resources.host_memory.provider import HostProvider
        from tests.host_memory_fixtures import NOW
        return HostProvider(read_text=lambda path: PROC_MEMINFO, stat=safe_stat,
                            run=lambda argv, timeout=5: calls.append(tuple(argv)) or command_result(),
                            target_identity=TARGET, now=lambda: NOW)

    def test_preflight_refuses_unsafe_matrix_without_side_effects(self):
        from datetime import datetime, timedelta, timezone
        from sandbox.resources.host_memory.policy import PolicyRefusal, build_plan
        from tests.host_memory_fixtures import NOW, eligible_state, service_evidence
        then = datetime(2026, 8, 30, 11, 31, tzinfo=timezone.utc)
        aged = eligible_state(observed_at="2026-08-30T11:30:00Z")
        cases = {
            "stale_plan": build_plan("enable", service_evidence(), aged, now=then),
            "foreign_target": build_plan("enable", {**service_evidence(),
                                          "target_identity": "other-host"},
                                         eligible_state(), now=NOW),
        }
        for name, plan in cases.items():
            with self.subTest(name=name):
                calls = []
                provider = self._preflight_provider(calls)
                with self.assertRaises(PolicyRefusal):
                    provider.preflight(plan)
                self.assertEqual(calls, [])

    def test_preflight_passes_eligible_without_touching_foreign_state(self):
        calls = []
        provider = self._preflight_provider(calls)
        self.assertEqual(provider.preflight(self._enable_plan()), "ready")
        self.assertEqual(calls, [])

    def test_sample_deadline_and_lock_no_overlap(self):
        calls = []
        provider = self._preflight_provider(calls)
        # Test hard 5-second deadline and timeout handling
        past_deadline = 0.0
        sample_res = provider.collect_sample(deadline=past_deadline)
        self.assertIn(sample_res["status"], {"partial", "failed"})
        self.assertIn("collector_timeout", sample_res.get("errors", ()))

    def test_sample_aggregates_without_sensitive_raw_data(self):
        calls = []
        provider = self._preflight_provider(calls)
        sample_res = provider.sample()
        forbidden_keys = {"stdout", "stderr", "output", "source_path", "processes", "argv", "path"}
        self.assertFalse(set(sample_res) & forbidden_keys)
        self.assertIn(sample_res["status"], {"valid", "partial", "failed"})

    def test_disable_transaction_enforces_reverse_order_and_preserves_history(self):
        calls = []
        provider = self._preflight_provider(calls)
        plan = {
            "operation": "disable",
            "plan_id": "a" * 64,
            "target": {"remote_name": "scaleway-sandbox", "target_identity": TARGET},
            "expires_at": "2026-08-30T12:15:00Z",
            "intended_changes": [
                "/etc/systemd/system/sandbox-host-memory-monitor.timer",
                "/etc/systemd/system/sandbox-host-memory-monitor.service",
                "/etc/sysctl.d/99-sandbox-swap.conf",
                "/etc/fstab",
                "/var/lib/sandbox/swap/swapfile",
            ],
            "rollback_scope": [
                "/etc/systemd/system/sandbox-host-memory-monitor.timer",
                "/etc/systemd/system/sandbox-host-memory-monitor.service",
                "/etc/sysctl.d/99-sandbox-swap.conf",
                "/etc/fstab",
                "/var/lib/sandbox/swap/swapfile",
            ],
        }
        res = provider.disable(plan)
        self.assertEqual(res["outcome"], "applied")
