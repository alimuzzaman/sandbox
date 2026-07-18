"""Application boundary for persistent and isolated workspace lifecycle."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class WorkspaceServiceProtocol(Protocol):
    def create(self, request): ...
    def list(self, request): ...
    def status(self, request): ...
    def reset(self, request): ...
    def destroy(self, request): ...


@dataclass
class WorkspaceService:
    """Persistent workspace metadata and explicit local lifecycle controls."""

    target_service: Any
    storage: Any | None = None
    remote_control: Any | None = None

    def _remote(self, target, action: str) -> dict | None:
        if target.kind != "remote":
            return None
        if self.remote_control is None:
            raise RuntimeError("remote workspace control is unavailable")
        return self.remote_control(target, action)

    def _root(self, target) -> Path:
        if self.storage is None:
            raise RuntimeError("workspace storage is unavailable")
        digest = target.namespace.replace(":", "-")
        root = self.storage.root / "workspaces" / digest
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root

    def create(self, request):
        target = self.target_service.resolve(request)
        remote = self._remote(target, "create")
        if remote is not None: return remote
        path = self._root(target) / target.workspace_label
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = {"label": target.workspace_label, "target": target.kind,
                    "remote": target.remote_name, "namespace": target.namespace,
                    "mode": "persistent", "path": str(path)}
        (path / "workspace.json").write_text(json.dumps(metadata, sort_keys=True) + "\n")
        return {"ok": True, "created": True, **metadata}

    def list(self, request):
        target = self.target_service.resolve(request)
        remote = self._remote(target, "list")
        if remote is not None: return remote
        root = self._root(target)
        items = []
        for path in sorted(root.iterdir()):
            metadata = path / "workspace.json"
            if metadata.exists():
                items.append(json.loads(metadata.read_text()))
        return {"ok": True, "workspaces": items, "namespace": target.namespace}

    def status(self, request):
        target = self.target_service.resolve(request)
        remote = self._remote(target, "status")
        if remote is not None: return remote
        path = self._root(target) / target.workspace_label / "workspace.json"
        if not path.exists():
            return {"ok": False, "code": "workspace_not_found", "label": target.workspace_label}
        return {"ok": True, **json.loads(path.read_text())}

    def reset(self, request):
        target = self.target_service.resolve(request)
        remote = self._remote(target, "reset")
        if remote is not None: return remote
        result = self.status(request)
        if not result.get("ok"):
            return result
        path = Path(result["path"])
        for child in path.iterdir():
            if child.name != "workspace.json":
                if child.is_dir(): shutil.rmtree(child)
                else: child.unlink()
        return {**result, "reset": True}

    def destroy(self, request):
        target = self.target_service.resolve(request)
        remote = self._remote(target, "destroy")
        if remote is not None: return remote
        result = self.status(request)
        if not result.get("ok"):
            return result
        shutil.rmtree(result["path"])
        return {"ok": True, "destroyed": True, "label": result["label"]}
