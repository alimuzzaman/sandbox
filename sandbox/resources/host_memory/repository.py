"""Owner-only atomic controller plans and host lifecycle evidence stores."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .models import AggregateMemorySample, HEX64, OwnershipReceipt, bounded


class RepositoryError(RuntimeError): pass


class HostMemoryRepository:
    def __init__(self, root: Path):
        self.root = Path(root); self.plans = self.root / "plans"

    def _atomic(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
        history = self.root / "history.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(AggregateMemorySample.from_dict(sample).to_dict(),
                          sort_keys=True, separators=(",", ":")) + "\n"
        encoded_size = len(line.encode())
        if encoded_size > maximum_bytes:
            raise RepositoryError("sample exceeds history bound")
        if history.exists() and history.stat().st_size + encoded_size > maximum_bytes:
            for index in range(8, 0, -1):
                source = self.root / ("history.%d.jsonl" % index)
                if index == 8:
                    try: source.unlink()
                    except FileNotFoundError: pass
                elif source.exists():
                    os.replace(source, self.root / ("history.%d.jsonl" % (index + 1)))
            os.replace(history, self.root / "history.1.jsonl")
        with history.open("a", encoding="utf-8") as stream:
            stream.write(line); stream.flush(); os.fsync(stream.fileno())
        files = sorted(self.root.glob("history*.jsonl"),
                       key=lambda path: (path.name == "history.jsonl", path.name))
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total <= maximum_bytes:
                break
            if path == history:
                continue
            total -= path.stat().st_size
            path.unlink()

    def history_window(self, since=None, until=None, limit=288):
        if isinstance(limit, bool) or not 1 <= int(limit) <= 1000: raise RepositoryError("invalid_limit")
        samples=[]; malformed=0
        for path in sorted(self.root.glob("history*.jsonl"))[:9]:
            try:
                for line in path.read_text().splitlines():
                    try:
                        row=AggregateMemorySample.from_dict(json.loads(line)).to_dict()
                    except (TypeError, ValueError): malformed += 1; continue
                    at=row.get("sampled_at", "")
                    if (since and at < since) or (until and at > until): continue
                    samples.append(bounded(row, 16 * 1024))
            except OSError: malformed += 1
        samples = sorted(samples, key=lambda x: x.get("sampled_at", ""), reverse=True)[:int(limit)]
        return bounded({"requested_range":{"since":since,"until":until},
            "observed_range":{"since":samples[-1]["sampled_at"],"until":samples[0]["sampled_at"]} if samples else None,
            "samples":samples,"counts":{"returned":len(samples),"malformed":malformed},
            "freshness":"unknown" if not samples else "observed","complete":malformed==0,
            "truncated":len(samples)>=int(limit)})
