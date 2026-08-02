"""Product-specific ingress adapters register through sandbox.ingress.manifest."""

from .detect_only import DetectOnlyAdapter
from .herd_valet import HerdSiteCompatibilityFacade

__all__ = ["DetectOnlyAdapter", "HerdSiteCompatibilityFacade"]
