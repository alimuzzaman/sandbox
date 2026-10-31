"""Composition root for the shared secret broker."""
from __future__ import annotations

from pathlib import Path

from .adapter_manifest import source_kind
from .authority import bind_provider_authorities
from .audit import SecretAudit
from .models import UseProfile
from .service import SecretService
from .sources import SourceRegistry


def build_secret_service(*, project_root, config, personal_path, runtime_root,
                         project_scope=None, provider_adapter=None,
                         credential_store=None) -> SecretService:
    secret_config = (config or {}).get("secrets", {})
    raw_sources = secret_config.get("sources", {})
    raw_profiles = secret_config.get("useProfiles", {})
    ownership = secret_config.get("ownership")
    if ownership is None:
        sources = bind_provider_authorities(
            raw_sources, raw_profiles, project_root=project_root,
        )
    else:
        sources = bind_provider_authorities(
            raw_sources, raw_profiles, project_root=project_root, ownership=ownership,
        )
    profiles = {}
    for name, item in raw_profiles.items():
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
                    prepare = getattr(credential_store, "prepare", None)
                    if callable(prepare):
                        prepare()
                except (ImportError, RuntimeError):
                    credential_store = None
    service = SecretService(
        SourceRegistry(project_root, sources, personal_path=personal_path, project_scope=project_scope),
        SecretAudit(root / "audit.jsonl"), revision_key_path=root / "revision.key",
        use_profiles=profiles, provider_adapter=provider_adapter,
        credential_store=credential_store,
    )
    if ownership is not None:
        # Attribute only; absent without an ownership block (no behaviour change).
        service.ownership = ownership
    return service


def build_ownership_context(project_dir: object):
    """Compose the ownership context: config, source registry and runtime root.

    Imports stay lazy so a plain secret service, and the ownership models,
    never require the project loader or the journal module.
    """
    from sandbox.application.context import load_project_descriptor
    from sandbox.core._paths import BASE
    from sandbox.core._secrets import secret_file
    from .models import SecretBrokerError
    from .ownership.journal import load_controller_id
    from .ownership.models import OwnershipConfig, OwnershipContext

    project_root = Path(project_dir).expanduser().resolve()
    config = load_project_descriptor(project_root)
    secret_config = (config or {}).get("secrets", {})
    ownership = secret_config.get("ownership")
    if ownership is None:
        raise SecretBrokerError(
            "ownership_not_configured", "secret ownership is not configured for this project",
        )
    sources = bind_provider_authorities(
        secret_config.get("sources", {}), secret_config.get("useProfiles", {}),
        project_root=project_root, ownership=ownership,
    )
    runtime_root = Path(BASE) / "runtime" / "secret-ownership"
    return OwnershipContext(
        config=OwnershipConfig.from_normalized(ownership),
        registry=SourceRegistry(project_root, sources, personal_path=secret_file()),
        project_root=project_root,
        runtime_root=runtime_root,
        controller_id=load_controller_id(runtime_root),
        read_only_broker=None,
        credential_store=None,
    )
