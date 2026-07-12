from typing import Protocol
import socket

class PortAllocator(Protocol):
    def allocate(self, preferred: int | None = None) -> int: ...


class SocketPortAllocator:
    def __init__(self, host: str = "127.0.0.1") -> None:
        self.host = host

    def allocate(self, preferred: int | None = None) -> int:
        candidates = (preferred,) if preferred is not None else (0,)
        for candidate in candidates:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((self.host, int(candidate)))
                return int(sock.getsockname()[1])
            except OSError:
                if preferred is not None:
                    raise ValueError(f"port {preferred} is unavailable")
            finally:
                sock.close()
        raise RuntimeError("no port available")
