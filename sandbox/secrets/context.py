"""Composition root for the shared secret broker."""
from __future__ import annotations

from pathlib import Path

from .adapter_manifest import source_kind
from .audit import SecretAudit
from .models import UseProfile
from .service import SecretService
from .sources import SourceRegistry


def build_secret_service(*, project_root, config, personal_path, runtime_root,
                         project_scope=None, provider_adapter=None,
                         credential_store=None) -> SecretService:
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
    # Keep provider and OS-store imports lazy for projects that use only file
    # sources. The manifests select only explicitly registered adapters.
    if provider_adapter is None or credential_store is None:
        has_infisical = any(
            source_kind(item) == "infisical"
            for item in sources.values()
        )
        if has_infisical:
            if provider_adapter is None:
                try:
                    from .providers.manifest import load_provider
                    provider_adapter = load_provider("infisical")()
                except (ImportError, RuntimeError):
                    provider_adapter = None
            if credential_store is None:
                try:
                    from .credentials.manifest import create_credential_store
                    credential_store = create_credential_store()
                except (ImportError, RuntimeError):
                    credential_store = None
    return SecretService(
        SourceRegistry(project_root, sources, personal_path=personal_path, project_scope=project_scope),
        SecretAudit(root / "audit.jsonl"), revision_key_path=root / "revision.key",
        use_profiles=profiles, provider_adapter=provider_adapter,
        credential_store=credential_store,
    )
