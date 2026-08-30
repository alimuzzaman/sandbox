"""Owner-only atomic controller plans and host lifecycle evidence stores."""

from __future__ import annotations

import json
import math
import os
import re
import stat as statmod
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from .models import AggregateMemorySample, HEX64, OwnershipReceipt, bounded, parse_utc, utc_text
from .policy import freshness, sustained_swap_use


class RepositoryError(RuntimeError): pass


class HostMemoryRepository:
    def __init__(self, root: Path, *, history_path: Path | None = None,
                 history_owner_uid: int | None = None, monotonic=None):
        self.root = Path(root); self.plans = self.root / "plans"
        self.history_path=Path(history_path) if history_path is not None else self.root/"history.jsonl"
        self.history_owner_uid=os.getuid() if history_owner_uid is None else history_owner_uid
        self.monotonic=monotonic or time.monotonic

    def _history_paths(self):
        return [self.history_path]+[Path(str(self.history_path)+f".{index}") for index in range(1,9)]

    def _deadline(self,budget_seconds):
        if (isinstance(budget_seconds,bool) or not isinstance(budget_seconds,(int,float))
                or not math.isfinite(float(budget_seconds))
                or budget_seconds<=0): raise RepositoryError("invalid_budget")
        return self.monotonic()+float(budget_seconds)

    def _check_deadline(self,deadline):
        if self.monotonic()>=deadline: raise RepositoryError("history_unavailable")

    def _attested_history_stat(self,path,deadline):
        self._check_deadline(deadline)
        try: info=path.lstat()
        except FileNotFoundError: return None
        except OSError: raise RepositoryError("history_unavailable") from None
        self._check_deadline(deadline)
        if (not statmod.S_ISREG(info.st_mode) or statmod.S_IMODE(info.st_mode)!=0o600
                or info.st_uid!=self.history_owner_uid or info.st_nlink!=1):
            raise RepositoryError("history_unavailable")
        return info

    def _atomic(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        encoded = json.dumps(bounded(payload), sort_keys=True, separators=(",", ":")).encode()
        fd, tmp = tempfile.mkstemp(prefix=".host-memory-", dir=str(path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            try: os.unlink(tmp)
            except FileNotFoundError: pass

    def save_plan(self, plan):
        plan_id = plan.get("plan_id")
        if not isinstance(plan_id, str) or not HEX64.fullmatch(plan_id): raise RepositoryError("invalid plan identity")
        path = self.plans / f"{plan_id}.json"
        if path.exists():
            if self.load_plan(plan_id) != plan: raise RepositoryError("plan identity collision")
            return
        self._atomic(path, plan)

    def load_plan(self, plan_id):
        if not isinstance(plan_id, str) or not HEX64.fullmatch(plan_id): raise RepositoryError("plan_not_found")
        try:
            data = json.loads((self.plans / f"{plan_id}.json").read_text())
        except (OSError, ValueError): raise RepositoryError("plan_not_found") from None
        if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("plan_id") != plan_id:
            raise RepositoryError("plan_not_found")
        return bounded(data)

    def save_operation(self, operation):
        if not isinstance(operation, dict) or operation.get("schema_version") != 1:
            raise RepositoryError("invalid operation evidence")
        operation_id = operation.get("operation_id")
        if not isinstance(operation_id, str) or not HEX64.fullmatch(operation_id):
            raise RepositoryError("invalid operation identity")
        current = self.load_operation()
        if current is not None and current.get("operation_id") != operation_id:
            raise RepositoryError("operation identity conflict")
        self._atomic(self.root / "operation.json", operation)
    def load_operation(self):
        try: data = json.loads((self.root / "operation.json").read_text())
        except FileNotFoundError: return None
        except (OSError, ValueError): raise RepositoryError("operation evidence is corrupt") from None
        return bounded(data)

    def save_receipt(self, receipt):
        model = OwnershipReceipt.from_dict(receipt)
        current = self.load_receipt()
        if current is not None and current["target_identity"] != model.target_identity:
            raise RepositoryError("ownership identity conflict")
        self._atomic(self.root / "receipt.json", model.to_dict())

    def load_receipt(self):
        try:
            data = json.loads((self.root / "receipt.json").read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise RepositoryError("ownership evidence is corrupt") from None
        try:
            return OwnershipReceipt.from_dict(data).to_dict()
        except (TypeError, ValueError):
            raise RepositoryError("ownership evidence is corrupt") from None

    def append_sample(self, sample, *, maximum_bytes=32 * 1024 * 1024):
        history = self.history_path
        history.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(history.parent,0o700)
        line = json.dumps(AggregateMemorySample.from_dict(sample).to_dict(),
                          sort_keys=True, separators=(",", ":")) + "\n"
        encoded_size = len(line.encode())
        if encoded_size > maximum_bytes:
            raise RepositoryError("sample exceeds history bound")
        if history.exists() and history.stat().st_size + encoded_size > maximum_bytes:
            for index in range(8, 0, -1):
                source = Path(str(history)+f".{index}")
                if index == 8:
                    try: source.unlink()
                    except FileNotFoundError: pass
                elif source.exists():
                    os.replace(source,Path(str(history)+f".{index+1}"))
            os.replace(history,Path(str(history)+".1"))
        with history.open("a", encoding="utf-8") as stream:
            stream.write(line); stream.flush(); os.fsync(stream.fileno())
        os.chmod(history,0o600)
        files=[path for path in self._history_paths() if path.exists()]
        total = sum(path.stat().st_size for path in files)
        for path in reversed(files):
            if total <= maximum_bytes:
                break
            if path == history:
                continue
            total -= path.stat().st_size
            path.unlink()

    def history_window(self, since=None, until=None, limit=288, *, budget_seconds=5,
                       deadline=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise RepositoryError("invalid_limit")
        deadline=self._deadline(budget_seconds) if deadline is None else deadline
        samples=[]; malformed=0; total_bytes=0
        for path in self._history_paths():
            try:
                info=self._attested_history_stat(path,deadline)
                if info is None: continue
                total_bytes+=info.st_size
                if total_bytes>32*1024*1024: raise RepositoryError("history_unavailable")
                text=path.read_text(encoding="utf-8")
                self._check_deadline(deadline)
                for line in text.splitlines():
                    self._check_deadline(deadline)
                    try:
                        row=AggregateMemorySample.from_dict(json.loads(line)).to_dict()
                    except (TypeError, ValueError): malformed += 1; continue
                    at=row.get("sampled_at", "")
                    if (since and at < since) or (until and at > until): continue
                    samples.append(bounded(row, 16 * 1024))
            except OSError: malformed += 1
        samples=sorted(samples,key=lambda x:x.get("sampled_at",""),reverse=True)
        truncated=len(samples)>limit
        samples=samples[:limit]
        try: return bounded({"requested_range":{"since":since,"until":until},
            "observed_range":{"since":samples[-1]["sampled_at"],"until":samples[0]["sampled_at"]} if samples else None,
            "samples":samples,"counts":{"returned":len(samples),"malformed":malformed},
            "freshness":"unknown" if not samples else "observed",
            "complete":malformed==0 and not truncated,
            "truncated":truncated})
        except ValueError: raise RepositoryError("history_unavailable") from None

    def status_monitor_evidence(self, *, now, interval_seconds=300,
                                budget_seconds=5,deadline=None):
        """Derive bounded monitor health from the retained aggregate history."""
        deadline=self._deadline(budget_seconds) if deadline is None else deadline
        window=self.history_window(limit=3,deadline=deadline)
        samples=window["samples"]
        latest=samples[0] if samples else None
        latest_at=latest.get("sampled_at") if latest else None
        paths=[]; total_bytes=0
        for path in self._history_paths():
            info=self._attested_history_stat(path,deadline)
            if info is not None: paths.append(path); total_bytes+=info.st_size
        current=self.history_path
        history_files=[path for path in paths if path!=current]
        retention={"current_files":1 if current.exists() else 0,
                   "history_files":len(history_files),"total_bytes":total_bytes,
                   "compliant":(len(history_files)<=8 and total_bytes<=32*1024*1024
                                and window["counts"]["malformed"]==0),
                   "truncated":bool(window["truncated"])}
        if latest_at is None:
            return {"latest_sample_at":None,"age_seconds":None,"freshness":"missing",
                    "next_sample_at":None,"sustained_swap_use":None,
                    "pressure_state":"unknown","retention":retention,
                    "history_complete":bool(window["complete"])}
        latest_time=parse_utc(latest_at)
        age=(now-latest_time).total_seconds()
        pressure=latest.get("pressure")
        pressure_state="unknown"
        if isinstance(pressure,dict):
            values=[]
            for group in ("some","full"):
                item=pressure.get(group)
                if isinstance(item,dict) and isinstance(item.get("avg10"),(int,float)):
                    values.append(float(item["avg10"]))
            if values: pressure_state="pressured" if any(value>0 for value in values) else "normal"
        return {"latest_sample_at":latest_at,
                "age_seconds":int(age) if age>=0 else None,
                "freshness":freshness(latest_at,now),
                "next_sample_at":utc_text(latest_time+timedelta(seconds=interval_seconds)),
                "sustained_swap_use":sustained_swap_use(list(reversed(samples))),
                "pressure_state":pressure_state,"retention":retention,
                "history_complete":bool(window["complete"])}
