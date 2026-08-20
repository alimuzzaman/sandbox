import math
import ipaddress
import urllib.error
from urllib.parse import urlsplit
import urllib.request
import http.client
import socket
from typing import Protocol

class HttpProbe(Protocol):
    def probe(self, url: str, *, timeout: float = 5) -> bool: ...


class UrlHttpProbe:
    def probe(self, url: str, *, timeout: float = 5) -> bool:
        if (not isinstance(url, str) or not url or
                any(ord(char) < 32 or ord(char) == 127 for char in url)):
            return False
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
                not math.isfinite(timeout) or timeout < 0):
            return False
        try:
            parsed = urlsplit(url)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                return False
        except ValueError:
            return False
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                return isinstance(status, int) and not isinstance(status, bool) and 200 <= status < 400
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return False

    @staticmethod
    def _route_inputs(address: str, port: int, host: str, timeout: float) -> tuple[str, int] | None:
        """Validate the exact, loopback-only route probe inputs."""
        if (not isinstance(address, str) or not isinstance(port, int)
                or isinstance(port, bool)):
            return None
        try:
            parsed_address = ipaddress.ip_address(address)
            parsed_port = port
        except Exception:
            return None
        if (not parsed_address.is_loopback or not 1 <= parsed_port <= 65535
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout < 0):
            return None
        if (not isinstance(host, str) or not host or len(host) > 253
                or any(ord(char) < 33 or ord(char) == 127 for char in host)):
            return None
        return str(parsed_address), parsed_port

    def probe_route_diagnostic(self, address: str, port: int, host: str, *,
                               timeout: float = 5, protocol: str = "http") -> dict:
        """Probe one exact selected-ingress endpoint with closed diagnostics.

        This deliberately uses ``http.client`` instead of urllib: it performs no
        resolver lookup, proxy discovery, or redirect following.  The result is
        a small status-class envelope; exception text, response headers, and
        response bodies never cross this boundary.
        """
        endpoint = self._route_inputs(address, port, host, timeout)
        if (endpoint is None or not isinstance(protocol, str)
                or protocol not in {"http", "https"}):
            return {
                "ingress": "unavailable", "application": "not_attempted",
                "reason": "ingress_probe_unavailable",
            }
        # Native HTTPS route probing requires a certificate/SNI policy that is
        # owned by the selected ingress adapter.  Do not silently downgrade it
        # to HTTP or use an unverified TLS context; report the bounded capability
        # gap instead.
        if protocol == "https":
            return {
                "ingress": "unavailable", "application": "not_attempted",
                "reason": "ingress_probe_unavailable",
            }
        parsed_address, parsed_port = endpoint
        try:
            connection = http.client.HTTPConnection(
                parsed_address, parsed_port, timeout=timeout,
            )
        except (socket.timeout, TimeoutError):
            return {
                "ingress": "timed_out", "application": "not_attempted",
                "reason": "ingress_connect_timeout",
            }
        except Exception:
            return {
                "ingress": "unreachable", "application": "not_attempted",
                "reason": "ingress_listener_unreachable",
            }
        try:
            try:
                # Split connect from request/response so a listener refusal and
                # an application response timeout remain distinguishable.
                connection.connect()
            except (socket.timeout, TimeoutError):
                return {
                    "ingress": "timed_out", "application": "not_attempted",
                    "reason": "ingress_connect_timeout",
                }
            except (OSError, http.client.HTTPException):
                return {
                    "ingress": "unreachable", "application": "not_attempted",
                    "reason": "ingress_listener_unreachable",
                }
            except Exception:
                return {
                    "ingress": "unreachable", "application": "not_attempted",
                    "reason": "ingress_listener_unreachable",
                }
            try:
                connection.request(
                    "GET", "/", headers={"Host": host, "Connection": "close"},
                )
                response = connection.getresponse()
            except (socket.timeout, TimeoutError):
                return {
                    "ingress": "reachable", "application": "timed_out",
                    "reason": "application_response_timeout",
                }
            except http.client.HTTPException:
                return {
                    "ingress": "reachable", "application": "protocol_error",
                    "reason": "ingress_probe_unavailable",
                }
            except OSError:
                return {
                    "ingress": "reachable", "application": "protocol_error",
                    "reason": "ingress_probe_unavailable",
                }
            except Exception:
                return {
                    "ingress": "reachable", "application": "protocol_error",
                    "reason": "ingress_probe_unavailable",
                }
            try:
                status = response.status
                healthy = (
                    isinstance(status, int) and not isinstance(status, bool)
                    and 200 <= status < 400
                )
            except Exception:
                status = None
                healthy = False
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            if status is None or not isinstance(status, int) or isinstance(status, bool):
                return {
                    "ingress": "reachable", "application": "protocol_error",
                    "reason": "ingress_probe_unavailable",
                }
            if not healthy:
                return {
                    "ingress": "reachable", "application": "http_unhealthy",
                    "reason": "application_http_unhealthy",
                }
            return {"ingress": "reachable", "application": "ready", "reason": "ready"}
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def probe_route(self, address: str, port: int, host: str, *, timeout: float = 5) -> bool:
        """Compatibility boolean for an exact HTTP endpoint probe."""
        try:
            result = self.probe_route_diagnostic(address, port, host, timeout=timeout)
            return (
                isinstance(result, dict)
                and result.get("ingress") == "reachable"
                and result.get("application") == "ready"
            )
        except Exception:
            return False
