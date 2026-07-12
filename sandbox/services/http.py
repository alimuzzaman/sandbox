from typing import Protocol
import urllib.error
import urllib.request

class HttpProbe(Protocol):
    def probe(self, url: str, *, timeout: float = 5) -> bool: ...


class UrlHttpProbe:
    def probe(self, url: str, *, timeout: float = 5) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return 200 <= response.status < 400
        except (urllib.error.URLError, TimeoutError, ValueError):
            return False
