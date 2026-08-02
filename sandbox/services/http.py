import math
import ipaddress
import urllib.error
from urllib.parse import urlsplit
import urllib.request
import http.client
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

    def probe_route(self, address: str, port: int, host: str, *, timeout: float = 5) -> bool:
        """Probe an exact HTTP endpoint without DNS, proxies, or redirects."""
        try:
            parsed_address = ipaddress.ip_address(address)
            parsed_port = int(port)
            if not parsed_address.is_loopback or not 1 <= parsed_port <= 65535:
                return False
            if (not isinstance(host, str) or not host or len(host) > 253
                    or any(ord(char) < 33 or ord(char) == 127 for char in host)):
                return False
            connection = http.client.HTTPConnection(
                str(parsed_address), parsed_port, timeout=timeout,
            )
            try:
                connection.request("GET", "/", headers={"Host": host,
                                                          "Connection": "close"})
                response = connection.getresponse()
                return 200 <= int(response.status) < 400
            finally:
                connection.close()
        except (ValueError, TypeError, OSError, TimeoutError, http.client.HTTPException):
            return False
