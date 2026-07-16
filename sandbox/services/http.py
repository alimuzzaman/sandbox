import math
import urllib.error
from urllib.parse import urlsplit
import urllib.request
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
