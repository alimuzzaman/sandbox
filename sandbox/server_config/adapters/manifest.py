"""Deterministic v1 server-config adapter manifest."""

from __future__ import annotations

from .base import AdapterDescriptor, AdapterRegistry


_DESCRIPTORS = (
    AdapterDescriptor(
        server_type="nginx",
        adapter_id="wordpress-cache/nginx/1",
        authority_versions=("wordpress-cache-v1",),
        renderer_revision="wordpress-cache-v1/nginx/1",
        active_image_families=("nginx",),
        web_service="nginx",
        mount_layout="server-config-mount-v1/nginx",
        readiness_contract="target-origin-effective-generation/v1",
    ),
    AdapterDescriptor(
        server_type="litespeed",
        adapter_id="wordpress-cache/openlitespeed/1",
        authority_versions=("wordpress-cache-v1",),
        renderer_revision="wordpress-cache-v1/openlitespeed/1",
        active_image_families=("litespeedtech/openlitespeed",),
        web_service="wp",
        mount_layout="server-config-mount-v1/openlitespeed-capability-gated",
        readiness_contract="target-origin-effective-vhost/v1",
    ),
)


def default_adapter_registry() -> AdapterRegistry:
    return AdapterRegistry(_DESCRIPTORS)
