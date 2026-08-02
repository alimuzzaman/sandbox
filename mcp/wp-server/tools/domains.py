"""Import-safe MCP transport for the shared domain application service."""

from __future__ import annotations

from dependencies import ToolDependencies


_domain_service = None
_ingress_service = None


def register(server, dependencies: ToolDependencies) -> None:
    global _domain_service, _ingress_service
    _domain_service = dependencies.require("domain_service")
    _ingress_service = dependencies.require("ingress_service")
    for tool in (
        domain_status, domain_plan, domain_apply, domain_cleanup, domain_support,
        ingress_status, ingress_support, ingress_plan, ingress_cleanup,
        ingress_reconcile, ingress_reconsider, ingress_apply,
    ):
        server.tool()(tool)


def _service():
    if _domain_service is None:
        raise RuntimeError("domain MCP group is not registered")
    return _domain_service()


def _ingress():
    if _ingress_service is None:
        raise RuntimeError("ingress MCP group is not registered")
    return _ingress_service()


def _payload(value):
    return value.to_dict() if hasattr(value, "to_dict") else value


def domain_status(project_dir: str, label: str = "default") -> dict:
    """Observe local hostname/resolver state without prompting or mutation."""
    return _payload(_service().status(project_dir, label=label))


def domain_plan(project_dir: str, label: str = "default") -> dict:
    """Plan scoped local name resolution without mutation."""
    return _payload(_service().plan(project_dir, label=label))


def domain_apply(project_dir: str, label: str = "default") -> dict:
    """Apply only pre-authorized domain state; never prompt for consent or privilege."""
    return _payload(_service().apply(project_dir, label=label, interactive=False))


def domain_cleanup(project_dir: str, label: str = "default") -> dict:
    """Remove unchanged owned domain state without prompting."""
    return _payload(_service().cleanup(project_dir, label=label, interactive=False))


def domain_support() -> dict:
    """List resolver implementation/proof tiers for this build."""
    return _payload(_service().support())


def ingress_status() -> dict:
    """Observe kernel listener scopes and best-effort product identity; never mutate."""
    return _payload(_ingress().detect())


def ingress_support() -> dict:
    """List ingress support tiers and live-proof gates."""
    return _payload(_ingress().support())


def ingress_plan(protocols: tuple[str, ...] = ("http", "https")) -> dict:
    """Select one candidate ingress without route, DNS, or credential mutation."""
    selection = _ingress().select(required_protocols=protocols)
    return {
        "ok": selection.adapter_id is not None, "operation": "ingress_plan",
        "state": "ready" if selection.adapter_id else "fallback",
        "ingress": selection.adapter_id, "pin": selection.pin,
        "pin_source": selection.pin_source,
        "accepted_addresses": list(selection.accepted_addresses),
        "reason": {"code": selection.reason_code}, "mutated": False,
    }


def _owner(project_dir: str, label: str) -> str:
    from pathlib import Path
    return f"{Path(project_dir).expanduser().resolve()}::{label}"


def ingress_cleanup(project_dir: str, label: str = "default") -> dict:
    """Retry conservative owned-route cleanup without prompting."""
    return _payload(_ingress().cleanup_owner(_owner(project_dir, label)))


def ingress_reconcile(project_dir: str, label: str = "default") -> dict:
    """Reconcile only durable incomplete ingress cleanup records; never prompt."""
    return _payload(_ingress().reconcile_owner(_owner(project_dir, label)))


def ingress_reconsider(consent_identity: str) -> dict:
    """Forget one remembered consent decision; route state is unchanged."""
    return _payload(_ingress().reconsider(consent_identity))


def ingress_apply() -> dict:
    """Refuse low-level apply: activation requires B's verified DNS handoff."""
    return {"ok": False, "operation": "ingress_apply",
            "state": "requires_domain_handoff", "mutated": False,
            "reason": {"code": "verified_naming_required"}}
