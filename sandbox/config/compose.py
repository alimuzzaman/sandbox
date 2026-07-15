from __future__ import annotations

import json
from pathlib import Path


class ComposeSchemaProvider:
    """Normalize the small, explicit Compose project descriptor."""

    def resolve(self, root: Path, *, label: str | None = None) -> dict:
        document = json.loads((root / "sandbox.config.json").read_text())
        if label:
            override = root / f"sandbox.config.{label}.json"
            if override.exists():
                document["compose"] = {**document.get("compose", {}), **json.loads(override.read_text()).get("compose", {})}
        compose = document.get("compose")
        if not isinstance(compose, dict):
            raise ValueError("compose project requires a compose descriptor")
        filename = compose.get("file")
        service = compose.get("service")
        port = compose.get("internal_port")
        health_path = compose.get("health_path")
        if not isinstance(filename, str) or not filename:
            raise ValueError("compose file is required")
        path = root / filename
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("compose file must stay within the project root") from exc
        if not isinstance(service, str) or not service:
            raise ValueError("compose service is required")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("compose internal_port must be a valid port")
        if not isinstance(health_path, str) or not health_path.startswith("/"):
            raise ValueError("compose health_path must start with /")
        return {"kind": "compose", "compose_file": str(path), "service": service,
                "internal_port": port, "health_path": health_path,
                "display_name": root.name, "label": label or "default"}
