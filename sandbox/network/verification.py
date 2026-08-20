"""Fresh resolver answer and ingress-backend verification."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from urllib.parse import urlsplit

from .detection import _answers
from .models import project_diagnostic


class DomainVerifier:
    def __init__(self, *, process, http, platform: str) -> None:
        self.process = process
        self.http = http
        self.platform = platform

    @staticmethod
    def _diagnostic_result(*, ingress: str, application: str,
                           reason: str) -> dict[str, dict[str, str]]:
        """Return the closed selected-ingress status classes."""
        return project_diagnostic(
            {"state": ingress}, {"state": application}, {"code": reason},
        )

    @staticmethod
    def _accepted_addresses(value: object) -> set[str] | None:
        """Normalize the selected ingress addresses without broadening scope."""
        if isinstance(value, (str, bytes, bytearray)):
            return None
        try:
            values = tuple(value or ())
        except Exception:
            return None
        addresses: set[str] = set()
        try:
            for item in values:
                if not isinstance(item, str):
                    return None
                address = ipaddress.ip_address(item)
                if not address.is_loopback:
                    return None
                addresses.add(str(address))
        except Exception:
            return None
        return addresses

    @staticmethod
    def _selected_target(value: object) -> tuple[str, int, str] | None:
        """Validate the internal selected-ingress endpoint description."""
        if not isinstance(value, Mapping):
            return None
        try:
            keys = set(value)
        except Exception:
            return None
        if keys != {"address", "port", "protocol"}:
            return None
        try:
            address_value = value.get("address")
            port_value = value.get("port")
            protocol = value.get("protocol")
        except Exception:
            return None
        if (not isinstance(address_value, str)
                or isinstance(port_value, bool) or not isinstance(port_value, int)
                or not isinstance(protocol, str) or protocol not in {"http", "https"}):
            return None
        try:
            address = ipaddress.ip_address(address_value)
        except Exception:
            return None
        if not address.is_loopback or not 1 <= port_value <= 65535:
            return None
        return str(address), port_value, protocol

    @staticmethod
    def _probe_observation(value: object) -> dict[str, dict[str, str]] | None:
        """Normalize the private flat probe result before public projection."""
        # UrlHttpProbe intentionally returns a flat, private transport result.
        # Nested maps or extra keys are treated as malformed rather than
        # recursively copied into the public result.
        if not isinstance(value, Mapping):
            return None
        try:
            if not {"ingress", "application", "reason"}.issubset(value):
                return None
            ingress = value.get("ingress")
            application = value.get("application")
            reason = value.get("reason")
        except Exception:
            return None
        if (not isinstance(ingress, str) or not isinstance(application, str)
                or not isinstance(reason, str)):
            return None
        try:
            return project_diagnostic(
                {"state": ingress}, {"state": application}, {"code": reason},
            )
        except Exception:
            return None

    def _resolve_fresh(self, hostname: str):
        command = (
            ("resolvectl", "query", "--cache=no", hostname)
            if self.platform == "linux" else
            ("dscacheutil", "-q", "host", "-a", "name", hostname)
        )
        try:
            result = self.process.run(command, timeout=5)
        except Exception:
            return None
        try:
            if getattr(result, "returncode", 1) != 0:
                return None
            return set(_answers(getattr(result, "stdout", "") or ""))
        except Exception:
            return None

    def diagnose(self, hostname: str, accepted_addresses: tuple[str, ...],
                 ingress: dict | None = None) -> dict:
        """Verify fresh DNS, then the selected ingress listener exactly.

        ``ingress`` is an internal, already-selected endpoint description.  It
        must contain a concrete loopback ``address``, bounded ``port``, and
        protocol.  A fallback URL is intentionally not accepted: status must
        never turn an application backend into a proxy/ingress health claim.
        """
        accepted = self._accepted_addresses(accepted_addresses)
        if accepted is None:
            return self._diagnostic_result(
                ingress="unavailable", application="not_attempted",
                reason="ingress_probe_unavailable",
            )
        target = self._selected_target(ingress)
        if target is None:
            return self._diagnostic_result(
                ingress="unavailable", application="not_attempted",
                reason="ingress_probe_unavailable",
            )
        address, port, protocol = target
        if address not in accepted:
            return self._diagnostic_result(
                ingress="unavailable", application="not_attempted",
                reason="ingress_probe_unavailable",
            )
        actual = self._resolve_fresh(hostname)
        if actual is None:
            return self._diagnostic_result(
                ingress="unavailable", application="not_attempted",
                reason="fresh_dns_unavailable",
            )
        if len(actual) != 1 or not actual.issubset(accepted):
            return self._diagnostic_result(
                ingress="unavailable", application="not_attempted",
                reason="answer_mismatch",
            )
        try:
            probe = getattr(self.http, "probe_route_diagnostic", None)
        except Exception:
            probe = None
        if probe is None:
            # Test doubles and legacy injected HTTP clients may only expose the
            # boolean compatibility wrapper.  Keep the fallback bounded and
            # classify it without exposing transport details.
            try:
                probe_bool = getattr(self.http, "probe_route", None)
            except Exception:
                probe_bool = None
            if probe_bool is None:
                return self._diagnostic_result(
                    ingress="unavailable", application="not_attempted",
                    reason="ingress_probe_unavailable",
                )
            try:
                healthy = bool(probe_bool(
                    address, port, hostname, timeout=5,
                ))
            except Exception:
                healthy = False
            return self._diagnostic_result(
                ingress="reachable" if healthy else "unreachable",
                application="ready" if healthy else "not_attempted",
                reason="ready" if healthy else "ingress_listener_unreachable",
            )
        try:
            observed = probe(
                address, port, hostname, timeout=5, protocol=protocol,
            )
        except Exception:
            observed = None
        projected = self._probe_observation(observed)
        if projected is None:
            return self._diagnostic_result(
                ingress="unavailable", application="not_attempted",
                reason="ingress_probe_unavailable",
            )
        return projected

    # Explicit aliases make the new structured operation discoverable while
    # leaving ``verify`` as the long-standing boolean API.
    verify_diagnostic = diagnose
    diagnostic = diagnose

    def verify(self, hostname: str, accepted_addresses: tuple[str, ...],
               fallback_url: str) -> bool:
        command = (
            ("resolvectl", "query", "--cache=no", hostname)
            if self.platform == "linux" else
            ("dscacheutil", "-q", "host", "-a", "name", hostname)
        )
        result = self.process.run(command, timeout=5)
        if result.returncode != 0:
            return False
        actual = set(_answers(result.stdout))
        if len(actual) != 1 or not actual.issubset(accepted_addresses):
            return False
        try:
            parsed = urlsplit(fallback_url)
            if parsed.scheme != "http" or parsed.username or parsed.password:
                return False
            fallback_host = parsed.hostname
            if fallback_host == "localhost":
                fallback_address = "127.0.0.1"
            else:
                candidate = ipaddress.ip_address(fallback_host or "")
                if not candidate.is_loopback:
                    return False
                fallback_address = str(candidate)
            fallback_port = parsed.port or 80
        except (ValueError, TypeError):
            return False
        probe_route = getattr(self.http, "probe_route", None)
        if probe_route is None:
            return False
        return bool(probe_route(
            fallback_address, fallback_port, hostname, timeout=5,
        ))
