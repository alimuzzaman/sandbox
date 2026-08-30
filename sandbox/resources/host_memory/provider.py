"""Fixed-path Linux provider. No request supplies paths, argv, or file bodies."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .models import AggregateMemorySample, OwnershipReceipt, RemoteSwapState, canonical_digest

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
    def __init__(self, *, read_text=None, stat=None, run=None, now=None,
                 target_identity=None):
        self.read_text=read_text or (lambda path: Path(path).read_text())
        self.stat=stat or (lambda path: Path(path).lstat())
        self.run=run or self._run
        self.now=now or (lambda: datetime.now(timezone.utc))
        self.target_identity=(target_identity or hashlib.sha256(
            platform.node().encode("utf-8", "replace")
        ).hexdigest()[:24])

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
                    "swap_limit_bytes": None, "swap_used_bytes": None,
                    "evidence_state": "partial"}
        try:
            v2_line = next((line for line in cgroup.splitlines() if line.startswith("0::")), None)
            if v2_line is not None:
                relative = v2_line[3:].strip().strip("/")
                if relative and (".." in relative.split("/") or "\x00" in relative):
                    raise ValueError("invalid cgroup locator")
                parts = relative.split("/") if relative else []
                prefixes = ["/sys/fs/cgroup" + ("/" + "/".join(parts[:index]) if index else "")
                            for index in range(len(parts), -1, -1)]
                memory_values=[]; swap_values=[]
                for prefix in prefixes:
                    memory_text=self._optional_text(prefix+"/memory.max")
                    swap_text=self._optional_text(prefix+"/memory.swap.max")
                    if memory_text is None or swap_text is None: raise ValueError
                    memory_values.append(self._bounded_integer(memory_text))
                    swap_values.append(self._bounded_integer(swap_text))
                leaf=prefixes[0]
                memory_used=self._bounded_integer(self._optional_text(leaf+"/memory.current"))
                swap_used=self._bounded_integer(self._optional_text(leaf+"/memory.swap.current"))
                if memory_used is None or swap_used is None: raise ValueError
                finite_memory=[value for value in memory_values if value is not None]
                finite_swap=[value for value in swap_values if value is not None]
                memory_limit=min(finite_memory) if finite_memory else None
                swap_limit=min(finite_swap) if finite_swap else None
                state = "limited" if memory_limit is not None or swap_limit is not None else "eligible"
                return {"state": state, "version": "v2", "memory_limit_bytes": memory_limit,
                        "memory_used_bytes": memory_used, "swap_limit_bytes": swap_limit,
                        "swap_used_bytes": swap_used, "evidence_state": "known"}
            v1_line = next((line for line in cgroup.splitlines() if ":memory:" in line), None)
            if v1_line is not None:
                relative = v1_line.split(":", 2)[2].strip().strip("/")
                if relative and (".." in relative.split("/") or "\x00" in relative):
                    raise ValueError("invalid cgroup locator")
                parts=relative.split("/") if relative else []
                prefixes=["/sys/fs/cgroup/memory"+("/"+"/".join(parts[:index]) if index else "")
                          for index in range(len(parts),-1,-1)]
                memory_values=[]; memsw_values=[]
                for prefix in prefixes:
                    memory_text=self._optional_text(prefix+"/memory.limit_in_bytes")
                    memsw_text=self._optional_text(prefix+"/memory.memsw.limit_in_bytes")
                    if memory_text is None or memsw_text is None: raise ValueError
                    memory_value=self._bounded_integer(memory_text)
                    memsw_value=self._bounded_integer(memsw_text)
                    # Cgroup v1 represents an unlimited value with a page-rounded
                    # number close to LONG_MAX rather than the v2 "max" token.
                    memory_values.append(None if memory_value >= 1 << 60 else memory_value)
                    memsw_values.append(None if memsw_value >= 1 << 60 else memsw_value)
                leaf=prefixes[0]
                memory_used=self._bounded_integer(self._optional_text(leaf+"/memory.usage_in_bytes"))
                memsw_used=self._bounded_integer(self._optional_text(leaf+"/memory.memsw.usage_in_bytes"))
                if memory_used is None or memsw_used is None: raise ValueError
                finite_memory=[value for value in memory_values if value is not None]
                finite_memsw=[value for value in memsw_values if value is not None]
                memory_limit=min(finite_memory) if finite_memory else None
                memsw_limit=min(finite_memsw) if finite_memsw else None
                swap_limit = (max(0, memsw_limit - memory_limit)
                              if memsw_limit is not None and memory_limit is not None else None)
                swap_used = (max(0, memsw_used - memory_used)
                             if memsw_used is not None and memory_used is not None else None)
                return {"state": "limited" if memory_limit is not None or memsw_limit is not None else "eligible",
                        "version": "v1", "memory_limit_bytes": memory_limit,
                        "memory_used_bytes": memory_used, "swap_limit_bytes": swap_limit,
                        "swap_used_bytes": swap_used, "evidence_state": "known"}
        except (TypeError, ValueError):
            pass
        return {"state": "unknown", "version": "unknown",
                "memory_limit_bytes": None, "memory_used_bytes": None,
                "swap_limit_bytes": None, "swap_used_bytes": None,
                "evidence_state": "partial"}

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
        retention={"current_files":0,"history_files":0,"total_bytes":0,
                   "compliant":True,"truncated":False}
        empty = {"observed_at": observed_at, "target_identity":self.target_identity,
            "memory": {"total_bytes": None, "available_bytes": None,"state":"unknown"},
            "filesystem": {"total_bytes": None, "free_bytes": None,"state":"unknown"},
            "swap_areas": [], "swappiness": {"effective": None, "owned": False,
            "drifted": False}, "monitor": {"service_state": "unknown",
            "timer_state": "unknown", "freshness": "unknown", "interval_seconds": None,
            "latest_sample_at": None,"age_seconds":None,"next_sample_at": None,
            "sustained_swap_use": None,"pressure_state": "unknown","retention":retention},
            "ownership": "unknown", "container_eligibility": {"state": "unsupported",
            "version":"unknown","memory_limit_bytes":None,"memory_used_bytes":None,
            "swap_limit_bytes":None,"swap_used_bytes":None,"evidence_state":"unsupported"},
            "reboot_verification": {"state":"unverified","observed_at":None},
            "operation_block": None}
        if platform.system() != "Linux":
            return RemoteSwapState.from_dict({**empty,"evidence_state":"unsupported"}).to_dict()
        complete = True
        try:
            memory = self._kv(self.read_text("/proc/meminfo"))
            swap_lines = self.read_text("/proc/swaps").splitlines()
            disk = shutil.disk_usage(STATE.parent)
        except (OSError, KeyError, TypeError, ValueError):
            return RemoteSwapState.from_dict({**empty,"container_eligibility":{
                "state":"unknown","version":"unknown","memory_limit_bytes":None,
                "memory_used_bytes":None,"swap_limit_bytes":None,"swap_used_bytes":None,
                "evidence_state":"partial"},"evidence_state":"partial"}).to_dict()
        memory_total = memory.get("MemTotal"); memory_available = memory.get("MemAvailable")
        if not isinstance(memory_total, int) or not isinstance(memory_available, int):
            complete = False
        receipt = None; receipt_malformed = False
        receipt_text = self._optional_text(RECEIPT)
        if receipt_text is not None:
            try: receipt = OwnershipReceipt.from_dict(json.loads(receipt_text)).to_dict()
            except (TypeError, ValueError): receipt_malformed = True
        receipt_target_match=bool(receipt and receipt["target_identity"]==self.target_identity)
        if receipt and not receipt_target_match: receipt_malformed=True
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
            owned_id=canonical_digest({"target_identity":self.target_identity,
                "logical_id":"swap_file","type":parts[1],"total_bytes":total,
                "priority":priority})[:24]
            area_id=(owned_id if parts[0]==str(SWAP) else canonical_digest({
                "target_identity":self.target_identity,"locator":parts[0],"type":parts[1],
                "total_bytes":total,"priority":priority})[:24])
            owned=bool(receipt_target_match and parts[0]==str(SWAP)
                       and receipt.get("swap_area_id")==owned_id)
            areas.append({"area_id": area_id, "type": parts[1], "total_bytes": total,
                          "used_bytes": used, "active": True,
                          "persistent": bool(owned and receipt["lifecycle_state"] == "enabled"),
                          "priority": priority, "ownership": "owned" if owned else "unmanaged"})
        unmanaged = any(area["ownership"] != "owned" for area in areas)
        if malformed_swap or receipt_malformed: complete = False
        monitor_owned = bool(receipt_target_match and receipt["lifecycle_state"] == "enabled")
        service_state = self._unit_state("sandbox-host-memory-monitor.service") if monitor_owned else "missing"
        timer_state = self._unit_state("sandbox-host-memory-monitor.timer") if monitor_owned else "missing"
        container=self._container_limits()
        if container["evidence_state"]!="known": complete=False
        result = {"observed_at": observed_at,"target_identity":self.target_identity,
            "memory": {"total_bytes": memory_total,"available_bytes":memory_available,
                       "state":"known" if memory_total is not None and memory_available is not None else "unknown"},
            "filesystem": {"total_bytes":disk.total,"free_bytes":disk.free,"state":"known"},
            "swap_areas": areas,
            "swappiness": {"effective": swappiness, "owned": monitor_owned,
                            "drifted": bool(monitor_owned and swappiness != 15)},
            "monitor": {"service_state": service_state, "timer_state": timer_state,
                        "freshness": "unknown", "interval_seconds": 300 if monitor_owned else None,
                        "latest_sample_at":None,"age_seconds":None,"next_sample_at":None,
                        "sustained_swap_use":None,"pressure_state":"unknown",
                        "retention":retention},
            "container_eligibility":container,
            "reboot_verification":((receipt or {}).get("reboot_verification")
                                   or {"state":"unverified","observed_at":None}),
            "operation_block": None,
            "ownership": "unknown" if receipt_malformed else "owned" if receipt else "absent",
            "evidence_state": ("unmanaged" if unmanaged else "partial" if not complete
                               else "drifted" if monitor_owned and swappiness != 15 else "known")}
        return RemoteSwapState.from_dict(result).to_dict()

    def sample(self):
        state=self.observe(); memory=state.get("memory") or {}; areas=state.get("swap_areas") or []
        complete=all(isinstance(memory.get(k),int) for k in ("total_bytes","available_bytes"))
        counters={key:value for key,value in memory.items() if key in {
            "total_bytes","available_bytes","free_bytes","buffers_bytes","cached_bytes"}
            and isinstance(value,int)}
        return AggregateMemorySample(sampled_at=self.now().isoformat().replace("+00:00","Z"),status="valid" if complete else "partial",memory=counters,swap={"total_bytes":sum(a.get("total_bytes",0) for a in areas),"free_bytes":sum(a.get("total_bytes",0)-a.get("used_bytes",0) for a in areas),"used_bytes":sum(a.get("used_bytes",0) for a in areas)},errors=() if complete else ("memory_evidence_partial",)).to_dict()
