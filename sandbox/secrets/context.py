"""Composition root for the shared secret broker."""
from __future__ import annotations

from pathlib import Path

from .audit import SecretAudit
from .models import UseProfile
from .service import SecretService
from .sources import SourceRegistry


def build_secret_service(*, project_root, config, personal_path, runtime_root,
                         project_scope=None) -> SecretService:
    secret_config = (config or {}).get("secrets", {})
    sources = secret_config.get("sources", {})
    profiles = {}
    for name, item in secret_config.get("useProfiles", {}).items():
        profiles[name] = UseProfile(
            name=name, source=item["source"], key=item["key"], argv=tuple(item["argv"]),
            destination=item["destination"], timeout_seconds=item.get("timeoutSeconds", 300),
            max_output_bytes=item.get("maxOutputBytes", 1_048_576), mcp=item.get("mcp", False),
        )
    root = Path(runtime_root) / "secrets"
    return SecretService(
        SourceRegistry(project_root, sources, personal_path=personal_path, project_scope=project_scope),
        SecretAudit(root / "audit.jsonl"), revision_key_path=root / "revision.key",
        use_profiles=profiles,
    )
