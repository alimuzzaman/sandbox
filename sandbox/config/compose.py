from __future__ import annotations

from pathlib import Path

from .descriptors import _load_mapping


class ComposeSchemaProvider:
    """Normalize the small, explicit Compose project descriptor."""

    def resolve(self, root: Path, *, label: str | None = None) -> dict:
        config_path = next((root / name for name in ("sandbox.config.json", "sandbox.config.yml", "sandbox.config.yaml") if (root / name).exists()), None)
        if config_path is None:
            raise ValueError("generic Compose project requires sandbox.config.json or sandbox.config.yml")
        document = _load_mapping(config_path)
        if label:
            override = next((root / f"sandbox.config.{label}{suffix}" for suffix in (".json", ".yml", ".yaml") if (root / f"sandbox.config.{label}{suffix}").exists()), None)
            if override is not None:
                override_doc = _load_mapping(override)
                document["compose"] = {**document.get("compose", {}), **override_doc.get("compose", {})}
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
        return {"kind": "compose", "framework": document.get("framework") or document.get("preset"),
                "compose_file": str(path), "service": service,
                "internal_port": port, "health_path": health_path,
                "http_port": compose.get("http_port"),
                "display_name": root.name, "label": label or "default",
                "root": str(root), "source": config_path.name}
