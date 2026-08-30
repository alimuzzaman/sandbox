"""Fixed-path Linux provider. No request supplies paths, argv, or file bodies."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import shutil
import stat as statmod
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import AggregateMemorySample, HEX64, OwnershipReceipt, RemoteSwapState, canonical_digest

STATE=Path("/var/lib/sandbox/host-memory")
SWAP=STATE/"sandbox.swap"
RECEIPT=STATE/"receipt.json"
JOURNAL=STATE/"operation.json"
HISTORY=Path("/var/log/sandbox/host-memory.jsonl")
LOCK=Path("/run/lock/sandbox-host-memory.lock")
SWAP_UNIT=Path("/etc/systemd/system/var-lib-sandbox-host\\x2dmemory-sandbox.swap.swap")
FIXED_ARTIFACTS={
 "swap_file":SWAP,
 "swap_unit":SWAP_UNIT,
 "swappiness_policy":Path("/etc/sysctl.d/90-sandbox-host-memory.conf"),
 "monitor_helper":Path("/usr/local/libexec/sandbox-host-memory-monitor"),
 "monitor_service":Path("/etc/systemd/system/sandbox-host-memory-monitor.service"),
 "monitor_timer":Path("/etc/systemd/system/sandbox-host-memory-monitor.timer"),
 "rotation_policy":Path("/etc/logrotate.d/sandbox-host-memory-monitor"),
}


class HostProvider:
    def __init__(self, *, read_text=None, stat=None, run=None, now=None,
                 target_identity=None, monotonic=None):
        self.read_text=read_text or (lambda path: Path(path).read_text())
        self.stat=stat or (lambda path: Path(path).lstat())
        self.run=run or self._run
        self.now=now or (lambda: datetime.now(timezone.utc))
        self.monotonic=monotonic or time.monotonic
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
        rows={}; required={"MemTotal","MemAvailable"}
        for line in text.splitlines():
            key=line.split(":",1)[0]
            if key not in required: continue
            parts=line.split()
            if (key in rows or len(parts)!=3 or parts[0]!=key+":"
                    or not parts[1].isdigit() or parts[2]!="kB"):
                raise ValueError("invalid memory evidence")
            rows[key]=int(parts[1])*1024
        return rows

    def _check_deadline(self, deadline):
        if deadline is not None and self.monotonic() >= deadline:
            raise TimeoutError("host observation budget exhausted")

    def _optional_text(self, path, deadline=None):
        self._check_deadline(deadline)
        try:
            value=self.read_text(path)
        except (OSError, KeyError):
            return None
        self._check_deadline(deadline)
        return value

    def _stat_info(self, path, deadline=None):
        self._check_deadline(deadline)
        try: info=self.stat(path)
        except (OSError, KeyError): return None
        self._check_deadline(deadline)
        return info

    def _safe_regular(self, path, mode, deadline=None):
        info=self._stat_info(path,deadline)
        return bool(info and statmod.S_ISREG(info.st_mode) and info.st_uid==0
                    and statmod.S_IMODE(info.st_mode)==mode and info.st_nlink==1)

    def _safe_receipt_path(self, deadline=None):
        try: RECEIPT.relative_to(STATE)
        except ValueError: return False
        root=self._stat_info(STATE,deadline)
        return bool(root and statmod.S_ISDIR(root.st_mode) and root.st_uid==0
                    and statmod.S_IMODE(root.st_mode)==0o700
                    and self._safe_regular(RECEIPT,0o600,deadline))

    def _artifact_attestation(self, receipt, deadline=None):
        artifacts=receipt.get("artifacts") if isinstance(receipt,dict) else None
        if not isinstance(artifacts,dict): return None
        requirements={"swap_file":(SWAP,0o600,None),
                      "swap_unit":(SWAP_UNIT,0o644,"enabled"),
                      "swappiness_policy":(FIXED_ARTIFACTS["swappiness_policy"],0o644,"active")}
        result={}
        for logical,(path,mode,state) in requirements.items():
            entry=artifacts.get(logical)
            if (not isinstance(entry,dict)
                    or set(entry)!={"kind","mode","digest","state"}
                    or entry.get("kind")!="regular" or entry.get("mode")!=mode
                    or not isinstance(entry.get("digest"),str)
                    or not HEX64.fullmatch(entry["digest"]) or not self._safe_regular(path,mode,deadline)):
                result[logical]=False; continue
            if state is not None and entry.get("state")!=state:
                result[logical]=False; continue
            if logical=="swap_file":
                result[logical]=True; continue
            content=self._optional_text(path,deadline)
            result[logical]=bool(content is not None and len(content.encode("utf-8"))<=64*1024
                and hashlib.sha256(content.encode("utf-8")).hexdigest()==entry["digest"])
        return result

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

    def _container_limits(self, deadline=None):
        cgroup = self._optional_text("/proc/self/cgroup",deadline)
        if cgroup is None:
            return {"state": "unknown", "version": "unknown",
                    "memory_limit_bytes": None, "memory_used_bytes": None,
                    "swap_limit_bytes": None, "swap_used_bytes": None,
                    "evidence_state": "partial"}
        try:
            v2_lines=[line for line in cgroup.splitlines() if line.startswith("0::")]
            v1_lines=[line for line in cgroup.splitlines() if ":memory:" in line]
            if len(v2_lines)>1 or len(v1_lines)>1 or (v2_lines and v1_lines):
                raise ValueError("contradictory cgroup locators")
            v2_line = v2_lines[0] if v2_lines else None
            if v2_line is not None:
                relative = v2_line[3:].strip().strip("/")
                if relative and (any(part in {"",".",".."} for part in relative.split("/"))
                                 or "\x00" in relative or "\\" in relative):
                    raise ValueError("invalid cgroup locator")
                parts = relative.split("/") if relative else []
                prefixes = ["/sys/fs/cgroup" + ("/" + "/".join(parts[:index]) if index else "")
                            for index in range(len(parts), -1, -1)]
                memory_values=[]; swap_values=[]
                for prefix in prefixes:
                    memory_text=self._optional_text(prefix+"/memory.max",deadline)
                    swap_text=self._optional_text(prefix+"/memory.swap.max",deadline)
                    if memory_text is None or swap_text is None: raise ValueError
                    memory_values.append(self._bounded_integer(memory_text))
                    swap_values.append(self._bounded_integer(swap_text))
                leaf=prefixes[0]
                memory_used=self._bounded_integer(self._optional_text(leaf+"/memory.current",deadline))
                swap_used=self._bounded_integer(self._optional_text(leaf+"/memory.swap.current",deadline))
                if memory_used is None or swap_used is None: raise ValueError
                finite_memory=[value for value in memory_values if value is not None]
                finite_swap=[value for value in swap_values if value is not None]
                memory_limit=min(finite_memory) if finite_memory else None
                swap_limit=min(finite_swap) if finite_swap else None
                state = "limited" if memory_limit is not None or swap_limit is not None else "eligible"
                return {"state": state, "version": "v2", "memory_limit_bytes": memory_limit,
                        "memory_used_bytes": memory_used, "swap_limit_bytes": swap_limit,
                        "swap_used_bytes": swap_used, "evidence_state": "known"}
            v1_line = v1_lines[0] if v1_lines else None
            if v1_line is not None:
                relative = v1_line.split(":", 2)[2].strip().strip("/")
                if relative and (any(part in {"",".",".."} for part in relative.split("/"))
                                 or "\x00" in relative or "\\" in relative):
                    raise ValueError("invalid cgroup locator")
                parts=relative.split("/") if relative else []
                prefixes=["/sys/fs/cgroup/memory"+("/"+"/".join(parts[:index]) if index else "")
                          for index in range(len(parts),-1,-1)]
                memory_values=[]; memsw_values=[]
                for prefix in prefixes:
                    memory_text=self._optional_text(prefix+"/memory.limit_in_bytes",deadline)
                    memsw_text=self._optional_text(prefix+"/memory.memsw.limit_in_bytes",deadline)
                    if memory_text is None or memsw_text is None: raise ValueError
                    memory_value=self._bounded_integer(memory_text)
                    memsw_value=self._bounded_integer(memsw_text)
                    # Normalize the v1 page-rounded LONG_MAX sentinel before
                    # comparing effective per-level limits.
                    memory_value=(None if memory_value is None or memory_value>=1<<60
                                  else memory_value)
                    memsw_value=(None if memsw_value is None or memsw_value>=1<<60
                                 else memsw_value)
                    if ((memory_value is None and memsw_value is not None)
                            or (memory_value is not None and memsw_value is not None
                                and memsw_value<memory_value)):
                        raise ValueError("contradictory cgroup v1 limits")
                    memory_values.append(memory_value)
                    memsw_values.append(memsw_value)
                leaf=prefixes[0]
                memory_used=self._bounded_integer(self._optional_text(leaf+"/memory.usage_in_bytes",deadline))
                memsw_used=self._bounded_integer(self._optional_text(leaf+"/memory.memsw.usage_in_bytes",deadline))
                if memory_used is None or memsw_used is None: raise ValueError
                if memsw_used<memory_used: raise ValueError("contradictory cgroup v1 usage")
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
        except (TypeError, ValueError, TimeoutError):
            pass
        return {"state": "unknown", "version": "unknown",
                "memory_limit_bytes": None, "memory_used_bytes": None,
                "swap_limit_bytes": None, "swap_used_bytes": None,
                "evidence_state": "partial"}

    def _unit_state(self, unit, deadline=None):
        try:
            self._check_deadline(deadline)
            timeout=5 if deadline is None else min(5,max(deadline-self.monotonic(),0))
            if timeout<=0: raise TimeoutError
            result = self.run(("systemctl", "is-active", unit), timeout=timeout)
            self._check_deadline(deadline)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            return "unknown"
        value = str(getattr(result, "stdout", "")).strip()
        if value == "active" and getattr(result, "returncode", 1) == 0:
            return "active"
        if value in {"inactive", "failed", "unknown"}:
            return "inactive" if value in {"inactive", "failed"} else "unknown"
        return "missing" if getattr(result, "returncode", 1) in {3, 4} else "unknown"

    def _unit_enabled(self,unit,deadline=None):
        try:
            self._check_deadline(deadline)
            timeout=5 if deadline is None else min(5,max(deadline-self.monotonic(),0))
            if timeout<=0: raise TimeoutError
            result=self.run(("systemctl","is-enabled",unit),timeout=timeout)
            self._check_deadline(deadline)
        except (OSError,subprocess.SubprocessError,TimeoutError): return False
        return getattr(result,"returncode",1)==0 and str(getattr(result,"stdout","")).strip()=="enabled"

    def observe(self, budget_seconds=15, *, deadline=None):
        if deadline is None:
            if (isinstance(budget_seconds,bool) or not isinstance(budget_seconds,(int,float))
                    or not math.isfinite(float(budget_seconds)) or budget_seconds<=0):
                raise ValueError("invalid observation budget")
            deadline=self.monotonic()+float(budget_seconds)
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
            memory_text=self._optional_text("/proc/meminfo",deadline)
            swaps_text=self._optional_text("/proc/swaps",deadline)
            if memory_text is None or swaps_text is None: raise ValueError
            memory = self._kv(memory_text)
            swap_lines = swaps_text.splitlines()
            if (not swap_lines or swap_lines[0].split()
                    != ["Filename","Type","Size","Used","Priority"]):
                raise ValueError("invalid proc swaps header")
            self._check_deadline(deadline)
            disk = shutil.disk_usage(STATE.parent)
            self._check_deadline(deadline)
        except (OSError, KeyError, TypeError, ValueError, TimeoutError):
            return RemoteSwapState.from_dict({**empty,"container_eligibility":{
                "state":"unknown","version":"unknown","memory_limit_bytes":None,
                "memory_used_bytes":None,"swap_limit_bytes":None,"swap_used_bytes":None,
                "evidence_state":"partial"},"evidence_state":"partial"}).to_dict()
        memory_total = memory.get("MemTotal"); memory_available = memory.get("MemAvailable")
        if not isinstance(memory_total, int) or not isinstance(memory_available, int):
            complete = False
        receipt = None; receipt_malformed = False; artifact_attestation=None
        receipt_info=self._stat_info(RECEIPT,deadline)
        if receipt_info is not None:
            if not self._safe_receipt_path(deadline): receipt_malformed=True
            else:
                receipt_text=self._optional_text(RECEIPT,deadline)
                try:
                    if receipt_text is None or len(receipt_text.encode("utf-8"))>128*1024: raise ValueError
                    receipt=OwnershipReceipt.from_dict(json.loads(receipt_text)).to_dict()
                    artifact_attestation=self._artifact_attestation(receipt,deadline)
                    if not artifact_attestation or not all(artifact_attestation.values()):
                        receipt_malformed=True
                except (TypeError,ValueError): receipt_malformed=True
        receipt_target_match=bool(receipt and receipt["target_identity"]==self.target_identity)
        if receipt and not receipt_target_match: receipt_malformed=True
        swappiness = None
        try: swappiness = self._bounded_integer(self._optional_text("/proc/sys/vm/swappiness",deadline))
        except ValueError: complete = False
        if swappiness is None: complete = False
        configuration_attested=bool(not receipt_malformed and receipt_target_match)
        swap_unit_enabled=(self._unit_enabled(SWAP_UNIT.name,deadline)
                           if configuration_attested and receipt["lifecycle_state"]=="enabled" else False)
        areas = []; malformed_swap = False; locators=set()
        for line in swap_lines[1:] if swap_lines else ():
            parts = line.split()
            try:
                if (len(parts) != 5 or parts[1] not in {"file", "partition"}
                        or parts[0] in locators): raise ValueError
                locators.add(parts[0])
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
            owned=bool(not receipt_malformed and receipt_target_match and parts[0]==str(SWAP)
                       and receipt.get("swap_area_id")==owned_id)
            areas.append({"area_id": area_id, "type": parts[1], "total_bytes": total,
                          "used_bytes": used, "active": True,
                          "persistent": bool(owned and artifact_attestation.get("swap_unit") and swap_unit_enabled
                                             and receipt["lifecycle_state"] == "enabled"),
                          "priority": priority, "ownership": "owned" if owned else "unmanaged"})
        unmanaged = any(area["ownership"] != "owned" for area in areas)
        if malformed_swap or receipt_malformed: complete = False
        monitor_owned = bool(not receipt_malformed and receipt_target_match
                             and receipt["lifecycle_state"] == "enabled")
        service_state = self._unit_state("sandbox-host-memory-monitor.service",deadline) if monitor_owned else "missing"
        timer_state = self._unit_state("sandbox-host-memory-monitor.timer",deadline) if monitor_owned else "missing"
        container=self._container_limits(deadline)
        if container["evidence_state"]!="known": complete=False
        configuration_drift=bool(monitor_owned and not any(
            area["ownership"]=="owned" and area["persistent"] for area in areas))
        result = {"observed_at": observed_at,"target_identity":self.target_identity,
            "memory": {"total_bytes": memory_total,"available_bytes":memory_available,
                       "state":"known" if memory_total is not None and memory_available is not None else "unknown"},
            "filesystem": {"total_bytes":disk.total,"free_bytes":disk.free,"state":"known"},
            "swap_areas": areas,
            "swappiness": {"effective": swappiness,
                            "owned":bool(monitor_owned and artifact_attestation.get("swappiness_policy")),
                            "drifted": bool(monitor_owned and artifact_attestation.get("swappiness_policy") and swappiness != 15)},
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
                               else "drifted" if configuration_drift or monitor_owned and swappiness != 15
                               else "known")}
        return RemoteSwapState.from_dict(result).to_dict()

    def sample(self):
        state=self.observe(); memory=state.get("memory") or {}; areas=state.get("swap_areas") or []
        complete=all(isinstance(memory.get(k),int) for k in ("total_bytes","available_bytes"))
        counters={key:value for key,value in memory.items() if key in {
            "total_bytes","available_bytes","free_bytes","buffers_bytes","cached_bytes"}
            and isinstance(value,int)}
        return AggregateMemorySample(sampled_at=self.now().isoformat().replace("+00:00","Z"),status="valid" if complete else "partial",memory=counters,swap={"total_bytes":sum(a.get("total_bytes",0) for a in areas),"free_bytes":sum(a.get("total_bytes",0)-a.get("used_bytes",0) for a in areas),"used_bytes":sum(a.get("used_bytes",0) for a in areas)},errors=() if complete else ("memory_evidence_partial",)).to_dict()
