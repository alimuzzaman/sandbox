from __future__ import annotations

from pathlib import Path
import re

from .descriptors import _load_mapping

_SAFE_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _valid_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


def _safe_text(value: object) -> bool:
    return isinstance(value, str) and not any(ord(char) < 32 or ord(char) == 127 for char in value)


class ComposeSchemaProvider:
    """Normalize the small, explicit Compose project descriptor."""

    def resolve(self, root: Path, *, label: str | None = None) -> dict:
        if label is not None and (not isinstance(label, str) or not _SAFE_LABEL.fullmatch(label)):
            raise ValueError("compose configuration label is invalid")
        config_path = next((root / name for name in ("sandbox.config.json", "sandbox.config.yml", "sandbox.config.yaml") if (root / name).exists()), None)
        if config_path is None:
            raise ValueError("generic Compose project requires sandbox.config.json or sandbox.config.yml")
        document = _load_mapping(config_path)
        if label:
            override = next((root / f"sandbox.config.{label}{suffix}" for suffix in (".json", ".yml", ".yaml") if (root / f"sandbox.config.{label}{suffix}").exists()), None)
            if override is not None:
                override_doc = _load_mapping(override)
                document["compose"] = {**document.get("compose", {}), **override_doc.get("compose", {})}
                document["runtime"] = {**document.get("runtime", {}), **override_doc.get("runtime", {})}
        compose = document.get("compose")
        if not isinstance(compose, dict):
            raise ValueError("compose project requires a compose descriptor")
        filename = compose.get("file")
        service = compose.get("service")
        port = compose.get("internal_port")
        health_path = compose.get("health_path")
        if not isinstance(filename, str) or not filename or not _safe_text(filename):
            raise ValueError("compose file is required")
        path = root / filename
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("compose file must stay within the project root") from exc
        if not isinstance(service, str) or not _SAFE_SERVICE.fullmatch(service):
            raise ValueError("compose service is required")
        if not _valid_port(port):
            raise ValueError("compose internal_port must be a valid port")
        if (not isinstance(health_path, str) or not health_path.startswith("/") or
                not _safe_text(health_path)):
            raise ValueError("compose health_path must start with /")
        http_port = compose.get("http_port")
        if http_port is not None and not _valid_port(http_port):
            raise ValueError("compose http_port must be a valid port")
        return {"kind": "compose", "framework": document.get("framework") or document.get("preset"),
                "compose_file": str(path), "service": service,
                "internal_port": port, "health_path": health_path,
                "http_port": http_port,
                "display_name": root.name, "label": label or "default",
                "runtime": document.get("runtime"),
                "root": str(root), "source": config_path.name}
