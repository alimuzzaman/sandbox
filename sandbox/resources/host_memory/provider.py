"""Fixed-path Linux provider. No request supplies paths, argv, or file bodies."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import AggregateMemorySample, OwnershipReceipt, bounded, canonical_digest
from .policy import PolicyRefusal, plan_current

STATE=Path("/var/lib/sandbox/host-memory")
SWAP=STATE/"sandbox.swap"
RECEIPT=STATE/"receipt.json"
JOURNAL=STATE/"operation.json"
HISTORY=Path("/var/log/sandbox/host-memory.jsonl")
LOCK=Path("/run/lock/sandbox-host-memory.lock")
FIXED_ARTIFACTS={
 "swap_file":SWAP,
 "swappiness_policy":Path("/etc/sysctl.d/90-sandbox-host-memory.conf"),
 "monitor_helper":Path("/usr/local/libexec/sandbox-host-memory-monitor"),
 "monitor_service":Path("/etc/systemd/system/sandbox-host-memory-monitor.service"),
 "monitor_timer":Path("/etc/systemd/system/sandbox-host-memory-monitor.timer"),
 "rotation_policy":Path("/etc/logrotate.d/sandbox-host-memory-monitor"),
}


class HostProvider:
    def __init__(self, *, read_text=None, stat=None, run=None, now=None):
        self.read_text=read_text or (lambda path: Path(path).read_text())
        self.stat=stat or (lambda path: Path(path).lstat())
        self.run=run or self._run
        self.now=now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _run(argv, timeout=5):
        # argv comes only from constants in this module. Never pass an environment.
        return subprocess.run(tuple(argv),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,timeout=timeout,check=False,env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LC_ALL":"C"})

    @staticmethod
    def _kv(text):
        rows={}
        for line in text.splitlines():
            parts=line.replace(":"," ").split()
            if len(parts)>=2 and parts[1].isdigit(): rows[parts[0]]=int(parts[1])*1024
        return rows

    def _optional_text(self, path):
        try:
            return self.read_text(path)
        except (OSError, KeyError):
            return None

    @staticmethod
    def _bounded_integer(text):
        if text is None:
            return None
        value = str(text).strip()
        if value == "max":
            return None
        if not value.isdigit():
            raise ValueError("invalid aggregate integer")
        return int(value)

    def _container_limits(self):
        cgroup = self._optional_text("/proc/self/cgroup")
        if cgroup is None:
            return {"state": "unknown", "version": "unknown",
                    "memory_limit_bytes": None, "memory_used_bytes": None,
                    "swap_limit_bytes": None, "swap_used_bytes": None}
        try:
            v2_line = next((line for line in cgroup.splitlines() if line.startswith("0::")), None)
            if v2_line is not None:
                relative = v2_line[3:].strip().strip("/")
                if relative and (".." in relative.split("/") or "\x00" in relative):
                    raise ValueError("invalid cgroup locator")
                prefix = "/sys/fs/cgroup" + ("/" + relative if relative else "")
                def v2(name):
                    value = self._optional_text(prefix + "/" + name)
                    if value is None and prefix != "/sys/fs/cgroup":
                        value = self._optional_text("/sys/fs/cgroup/" + name)
                    return self._bounded_integer(value)
                memory_limit = v2("memory.max")
                memory_used = v2("memory.current")
                swap_limit = v2("memory.swap.max")
                swap_used = v2("memory.swap.current")
                state = "limited" if memory_limit is not None or swap_limit is not None else "eligible"
                return {"state": state, "version": "v2", "memory_limit_bytes": memory_limit,
                        "memory_used_bytes": memory_used, "swap_limit_bytes": swap_limit,
                        "swap_used_bytes": swap_used}
            v1_line = next((line for line in cgroup.splitlines() if ":memory:" in line), None)
            if v1_line is not None:
                relative = v1_line.split(":", 2)[2].strip().strip("/")
                if relative and (".." in relative.split("/") or "\x00" in relative):
                    raise ValueError("invalid cgroup locator")
                prefix = "/sys/fs/cgroup/memory" + ("/" + relative if relative else "")
                def v1(name):
                    value = self._optional_text(prefix + "/" + name)
                    if value is None and prefix != "/sys/fs/cgroup/memory":
                        value = self._optional_text("/sys/fs/cgroup/memory/" + name)
                    return self._bounded_integer(value)
                memory_limit = v1("memory.limit_in_bytes")
                memory_used = v1("memory.usage_in_bytes")
                memsw_limit = v1("memory.memsw.limit_in_bytes")
                memsw_used = v1("memory.memsw.usage_in_bytes")
                swap_limit = (max(0, memsw_limit - memory_limit)
                              if memsw_limit is not None and memory_limit is not None else None)
                swap_used = (max(0, memsw_used - memory_used)
                             if memsw_used is not None and memory_used is not None else None)
                return {"state": "limited" if memory_limit is not None else "unknown",
                        "version": "v1", "memory_limit_bytes": memory_limit,
                        "memory_used_bytes": memory_used, "swap_limit_bytes": swap_limit,
                        "swap_used_bytes": swap_used}
        except (TypeError, ValueError):
            pass
        return {"state": "unknown", "version": "unknown",
                "memory_limit_bytes": None, "memory_used_bytes": None,
                "swap_limit_bytes": None, "swap_used_bytes": None}

    def _unit_state(self, unit):
        try:
            result = self.run(("systemctl", "is-active", unit), timeout=5)
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        value = str(getattr(result, "stdout", "")).strip()
        if value == "active" and getattr(result, "returncode", 1) == 0:
            return "active"
        if value in {"inactive", "failed", "unknown"}:
            return "inactive" if value in {"inactive", "failed"} else "unknown"
        return "missing" if getattr(result, "returncode", 1) in {3, 4} else "unknown"

    def observe(self):
        observed_at = self.now().isoformat().replace("+00:00", "Z")
        empty = {"observed_at": observed_at, "memory": {"total_bytes": None,
            "available_bytes": None}, "filesystem": {"total_bytes": None, "free_bytes": None},
            "swap_areas": [], "swappiness": {"effective": None, "owned": False,
            "drifted": False}, "monitor": {"service_state": "unknown",
            "timer_state": "unknown", "freshness": "unknown", "interval_seconds": None,
            "latest_sample_at": None, "next_sample_at": None, "sustained_swap_use": None,
            "pressure_state": "unknown"}, "ownership": "unknown",
            "container_eligibility": {"state": "unsupported"},
            "reboot_verification": "unverified", "operation_block": None}
        if platform.system() != "Linux":
            return bounded({**empty, "evidence_state": "unsupported"}, 256 * 1024)
        complete = True
        try:
            memory = self._kv(self.read_text("/proc/meminfo"))
            swap_lines = self.read_text("/proc/swaps").splitlines()
            disk = shutil.disk_usage(STATE.parent)
        except (OSError, KeyError, TypeError, ValueError):
            return bounded({**empty, "container_eligibility": {"state": "unknown"},
                            "evidence_state": "partial"}, 256 * 1024)
        memory_total = memory.get("MemTotal"); memory_available = memory.get("MemAvailable")
        if not isinstance(memory_total, int) or not isinstance(memory_available, int):
            complete = False
        receipt = None; receipt_malformed = False
        receipt_text = self._optional_text(RECEIPT)
        if receipt_text is not None:
            try: receipt = OwnershipReceipt.from_dict(json.loads(receipt_text)).to_dict()
            except (TypeError, ValueError): receipt_malformed = True
        swappiness = None
        try: swappiness = self._bounded_integer(self._optional_text("/proc/sys/vm/swappiness"))
        except ValueError: complete = False
        if swappiness is None: complete = False
        areas = []; malformed_swap = False
        for line in swap_lines[1:] if swap_lines else ():
            parts = line.split()
            try:
                if len(parts) != 5 or parts[1] not in {"file", "partition"}: raise ValueError
                total = int(parts[2]) * 1024; used = int(parts[3]) * 1024; priority = int(parts[4])
                if total < 0 or used < 0 or used > total: raise ValueError
            except ValueError:
                malformed_swap = True; continue
            area_id = canonical_digest({"type": parts[1], "total_bytes": total,
                                        "priority": priority})[:24]
            owned = bool(receipt and receipt.get("swap_area_id") == area_id)
            areas.append({"area_id": area_id, "type": parts[1], "total_bytes": total,
                          "used_bytes": used, "active": True,
                          "persistent": bool(owned and receipt["lifecycle_state"] == "enabled"),
                          "priority": priority, "ownership": "owned" if owned else "unmanaged"})
        unmanaged = any(area["ownership"] != "owned" for area in areas)
        if malformed_swap or receipt_malformed: complete = False
        monitor_owned = bool(receipt and receipt["lifecycle_state"] == "enabled")
        service_state = self._unit_state("sandbox-host-memory-monitor.service") if monitor_owned else "missing"
        timer_state = self._unit_state("sandbox-host-memory-monitor.timer") if monitor_owned else "missing"
        target_identity = receipt.get("target_identity") if receipt else None
        result = {"observed_at": observed_at,
            "memory": {"total_bytes": memory_total, "available_bytes": memory_available},
            "filesystem": {"total_bytes": disk.total, "free_bytes": disk.free},
            "swap_areas": areas,
            "swappiness": {"effective": swappiness, "owned": monitor_owned,
                            "drifted": bool(monitor_owned and swappiness != 15)},
            "monitor": {"service_state": service_state, "timer_state": timer_state,
                        "freshness": "unknown", "interval_seconds": 300 if monitor_owned else None,
                        "latest_sample_at": None, "next_sample_at": None,
                        "sustained_swap_use": None, "pressure_state": "unknown"},
            "container_eligibility": self._container_limits(),
            "reboot_verification": (receipt or {}).get("reboot_verification", "unverified"),
            "operation_block": None,
            "ownership": "unknown" if receipt_malformed else "owned" if receipt else "absent",
            "evidence_state": ("unmanaged" if unmanaged else "partial" if not complete
                               else "drifted" if monitor_owned and swappiness != 15 else "known")}
        if target_identity: result["target_identity"] = target_identity
        return bounded(result, 256 * 1024)

    def sample(self):
        state=self.observe(); memory=state.get("memory") or {}; areas=state.get("swap_areas") or []
        complete=all(isinstance(memory.get(k),int) for k in ("total_bytes","available_bytes"))
        return AggregateMemorySample(sampled_at=self.now().isoformat().replace("+00:00","Z"),status="valid" if complete else "partial",memory=memory,swap={"total_bytes":sum(a.get("total_bytes",0) for a in areas),"free_bytes":sum(a.get("total_bytes",0)-a.get("used_bytes",0) for a in areas),"used_bytes":sum(a.get("used_bytes",0) for a in areas)},errors=() if complete else ("memory_evidence_partial",)).to_dict()

    def apply(self, plan, operation_id):
        # This narrow provider is intentionally fail-closed until root preflight proves every
        # fixed ancestor/artifact. Synthetic tests inject a provider for transaction proof.
        current=self.observe(); plan_current(plan,current)
        if os.geteuid()!=0: raise PolicyRefusal("required_facility_unavailable","fixed host provider requires root service authority")
        for path in FIXED_ARTIFACTS.values():
            if path.exists() and path.is_symlink(): raise PolicyRefusal("unsafe_swap_artifact","owned artifact cannot be a symlink")
        raise PolicyRefusal("required_facility_unavailable","live mutation requires separately accepted provider revision")
