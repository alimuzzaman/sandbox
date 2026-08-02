from __future__ import annotations

from collections.abc import Callable, Mapping

from .base import OperationRequest, OperationResult


# These names are intentionally operation-level contracts, not transport or
# product names.  A caller can discover the same envelope from Compose or a
# native backend before deciding whether to make a request.
OPTIONAL_WORDPRESS_CAPABILITIES = frozenset({
    "stop", "logs", "wordpress.snapshot", "wordpress.mail", "wordpress.debug",
    "wordpress.multisite", "wordpress.server-switch", "wordpress.remote-deploy",
    "wordpress.remote-preview",
})

SAFE_ALTERNATIVES = {
    "stop": "Use destroy for an explicit managed teardown.",
    "logs": "Use status and instance-specific log access.",
    "wordpress.snapshot": "Use an explicit export before recreating the instance.",
    "wordpress.mail": "Use the configured external mail capture service.",
    "wordpress.debug": "Use bounded logs and status diagnostics.",
    "wordpress.multisite": "Use a supported single-site configuration.",
    "wordpress.server-switch": "Export, recreate in the explicit mode, then import.",
    "wordpress.remote-deploy": "Use Compose remote deployment.",
    "wordpress.remote-preview": "Use Compose remote deployment.",
}


def capability_envelope(adapter) -> dict[str, dict[str, dict[str, object]]]:
    """Return a stable required/optional capability declaration.

    Adapters may explicitly publish their required set.  Existing adapters are
    compatible: their declared capabilities are required except for the small,
    named optional surface above.  Unsupported optional operations include a
    non-mutating alternative rather than leaving callers to probe a transport.
    """
    supported = frozenset(getattr(adapter, "capabilities", ()))
    optional = frozenset(getattr(adapter, "optional_capabilities", ()))
    optional = optional | (supported & OPTIONAL_WORDPRESS_CAPABILITIES)
    required = frozenset(getattr(adapter, "required_capabilities", ()))
    if not required:
        required = supported - optional
    names = optional | required | OPTIONAL_WORDPRESS_CAPABILITIES
    return {
        "required": {
            name: {"supported": name in supported}
            for name in sorted(required)
        },
        "optional": {
            name: {
                "supported": name in supported,
                **({"alternative": SAFE_ALTERNATIVES[name]}
                   if name not in supported and name in SAFE_ALTERNATIVES else {}),
            }
            for name in sorted(names - required)
        },
    }


def safe_alternative(capability: str) -> str | None:
    return SAFE_ALTERNATIVES.get(capability)


class WordPressAdapter:
    adapter_id = "wordpress"
    kinds = ("wordpress",)

    def __init__(self, operations: Mapping[str, Callable], *, capabilities=()) -> None:
        self._operations = dict(operations)
        self.capabilities = frozenset((*self._operations, *capabilities))
        self.optional_capabilities = self.capabilities & OPTIONAL_WORDPRESS_CAPABILITIES
        self.required_capabilities = self.capabilities - self.optional_capabilities

    def invoke(self, request: OperationRequest) -> OperationResult:
        handler = self._operations.get(request.operation)
        if handler is None:
            # Capability discovery can precede the transport migration for a
            # legacy operation.  Preserve a typed, non-mutating limitation
            # rather than leaking a KeyError or guessing a host fallback.
            return OperationResult(
                ok=False,
                operation=request.operation,
                project_root=request.project_root,
                project_kind="wordpress",
                data={"state": "unsupported", "mutated": False,
                      "reason": {"code": "operation_dispatch_unavailable"}},
            )
        value = handler(request)
        if isinstance(value, OperationResult):
            return value
        if not isinstance(value, dict):
            value = {"value": value}
        return OperationResult(
            ok=bool(value.get("ok", True)),
            operation=request.operation,
            project_root=request.project_root,
            project_kind="wordpress",
            data=value,
        )
