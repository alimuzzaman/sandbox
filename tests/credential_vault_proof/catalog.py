"""Immutable typed catalog for every proof check and retained artifact."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class CheckExpectation:
    check_id: str
    category: str
    source: str
    expectation: str = "exit_zero"
    required: bool = True
    predicate: str = "predicate_unavailable"


_TYPED_PREDICATES = {
    "os_release_supported": "exact_text",
    "kernel_release_expected": "exact_text",
    "architecture_expected": "exact_text",
    "sandbox_revision_expected": "exact_text",
    "service_account_expected": "exact_text",
    "unit_identity_expected": "unit_identity",
    "unit_ownership_expected": "unit_ownership",
    "broker_process_identity": "process_identity",
    "controller_process_identity": "process_identity",
    "executable_ownership_expected": "executable_ownership",
    "cgroup_identity_expected": "systemd_fields",
    "lease_socket_owned": "socket_owner",
    "controller_socket_owned": "socket_owner",
    "guest_listener_bound": "socket_owner",
    "veth_identity_expected": "link_identity",
    "veth_address_expected": "interface_address",
    "route_table_expected": "route",
    "nftables_default_drop": "nftables_policy",
    "apparmor_profile_enforced": "apparmor_profile",
    "no_unexpected_host_mount": "mount_isolation",
    "unit_absent_after_cleanup": "unit_absent",
    "socket_absent_after_cleanup": "empty_output",
}


def _check(check_id: str, category: str, source: str = "host_command",
           expectation: str = "exit_zero", required: bool = True
           ) -> CheckExpectation:
    predicate = _TYPED_PREDICATES.get(check_id)
    if predicate is None and source != "host_command":
        predicate = "typed_source"
    if predicate is None and expectation == "exit_nonzero":
        predicate = "not_found_exit"
    return CheckExpectation(check_id, category, source, expectation, required,
                            predicate or "predicate_unavailable")


_CHECKS = (
    _check("os_release_supported", "platform"),
    _check("kernel_release_expected", "platform"),
    _check("architecture_expected", "platform"),
    _check("sandbox_revision_expected", "revision"),
    _check("unit_identity_expected", "service_identity"),
    _check("unit_ownership_expected", "service_identity"),
    _check("service_account_expected", "service_identity"),
    _check("broker_process_identity", "process_identity"),
    _check("controller_process_identity", "process_identity"),
    _check("executable_ownership_expected", "service_identity"),
    _check("cgroup_identity_expected", "process_identity"),
    _check("lease_socket_owned", "transport"),
    _check("controller_socket_owned", "transport"),
    _check("guest_listener_bound", "transport"),
    _check("veth_identity_expected", "network"),
    _check("veth_address_expected", "network"),
    _check("route_table_expected", "network", required=False),
    _check("nftables_default_drop", "network"),
    _check("apparmor_profile_enforced", "service_identity"),
    _check("no_unexpected_host_mount", "service_identity"),
    _check("peer_credentials_observed", "descriptor", "broker_status"),
    _check("scm_credentials_observed", "descriptor", "broker_status"),
    _check("scm_rights_exactly_one", "descriptor", "broker_status"),
    _check("memfd_type_and_seals", "descriptor", "broker_status"),
    _check("descriptor_closed_on_success", "descriptor", "broker_status"),
    _check("descriptor_closed_on_failure", "descriptor", "broker_status"),
    _check("guest_cannot_reach_controller", "network", "guest_probe"),
    _check("guest_cannot_reach_lease_socket", "network", "guest_probe"),
    _check("guest_cannot_reach_host", "network", "guest_probe"),
    _check("guest_cannot_reach_loopback", "network", "guest_probe"),
    _check("guest_cannot_reach_metadata", "network", "guest_probe"),
    _check("guest_cannot_reach_other_interface", "network", "guest_probe"),
    _check("bindtodevice_enforced", "network", "guest_probe"),
    _check("dns_pinning_enforced", "upstream", "guest_probe"),
    _check("tls_verification_enforced", "upstream", "guest_probe"),
    _check("redirect_refused", "upstream", "guest_probe"),
    _check("response_size_bounded", "bounds", "guest_probe"),
    _check("request_timeout_bounded", "bounds", "guest_probe"),
    _check("concurrency_ceiling_enforced", "bounds", "guest_probe"),
    _check("epoch_rotates_on_restart", "lifecycle", "broker_status"),
    _check("quiesce_before_drain", "lifecycle", "broker_status"),
    _check("drain_precedes_stop", "lifecycle", "broker_status"),
    _check("unit_absent_after_cleanup", "cleanup"),
    _check("process_absent_after_cleanup", "cleanup", expectation="exit_nonzero"),
    _check("route_absent_after_cleanup", "cleanup", expectation="exit_nonzero"),
    _check("nftables_absent_after_cleanup", "cleanup", expectation="exit_nonzero"),
    _check("socket_absent_after_cleanup", "cleanup", expectation="empty_output"),
    _check("interface_absent_after_cleanup", "cleanup", expectation="exit_nonzero"),
    _check("cgroup_absent_after_cleanup", "cleanup", expectation="exit_nonzero"),
    _check("temporary_absent_after_cleanup", "cleanup", expectation="exit_nonzero"),
    _check("descriptor_absent_after_cleanup", "cleanup", "broker_status"),
)

CHECKS = MappingProxyType({item.check_id: item for item in _CHECKS})


@dataclass(frozen=True, slots=True)
class ArtifactExpectation:
    name: str
    media_type: str
    maximum_bytes: int


ARTIFACTS = MappingProxyType({
    "checks.json": ArtifactExpectation("checks.json", "check-executions-v1", 262144),
    "cleanup.json": ArtifactExpectation("cleanup.json", "cleanup-observations-v1", 65536),
})
EXPECTED_CLEANUP_PATHS = ("/run/sandbox-native/credential-broker",)


__all__ = [
    "ARTIFACTS", "CHECKS", "EXPECTED_CLEANUP_PATHS", "ArtifactExpectation",
    "CheckExpectation",
]
