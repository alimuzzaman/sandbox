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

from .models import AggregateMemorySample, canonical_digest
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

    def observe(self):
        if platform.system()!="Linux": return {"observed_at":self.now().isoformat(),"memory":{},"filesystem":{},"swap_areas":[],"monitor":{},"ownership":"unknown","evidence_state":"unsupported","container_eligibility":{"state":"unsupported"},"reboot_verification":"unverified","operation_block":None}
        try: memory=self._kv(self.read_text("/proc/meminfo")); swaps=self.read_text("/proc/swaps").splitlines()[1:]; disk=shutil.disk_usage(STATE.parent)
        except (OSError,ValueError): return {"observed_at":self.now().isoformat(),"memory":{},"filesystem":{},"swap_areas":[],"monitor":{},"ownership":"unknown","evidence_state":"partial","container_eligibility":{"state":"unknown"},"reboot_verification":"unverified","operation_block":None}
        receipt=None
        try: receipt=json.loads(self.read_text(RECEIPT))
        except (OSError,ValueError): pass
        areas=[]
        for index,line in enumerate(swaps):
            parts=line.split()
            if len(parts)<5: continue
            total=int(parts[2])*1024; used=int(parts[3])*1024
            owned=bool(receipt and receipt.get("swap_area_id")==canonical_digest({"index":index,"total":total})[:24])
            areas.append({"area_id":canonical_digest({"index":index,"total":total})[:24],"type":"file" if parts[1]=="file" else "partition","total_bytes":total,"used_bytes":used,"active":True,"persistent":"unknown","priority":int(parts[4]),"ownership":"owned" if owned else "unmanaged"})
        unmanaged=any(x["ownership"]!="owned" for x in areas)
        return {"observed_at":self.now().isoformat().replace("+00:00","Z"),
            "memory":{"total_bytes":memory.get("MemTotal"),"available_bytes":memory.get("MemAvailable")},
            "filesystem":{"total_bytes":disk.total,"free_bytes":disk.free},"swap_areas":areas,
            "swappiness":{"effective":None,"owned":bool(receipt),"drifted":False},
            "monitor":{"service_state":"unknown","timer_state":"unknown","freshness":"unknown","interval_seconds":300},
            "container_eligibility":{"state":"unknown"},"reboot_verification":"unverified",
            "operation_block":None,"ownership":"owned" if receipt else "absent",
            "evidence_state":"unmanaged" if unmanaged else "known"}

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
