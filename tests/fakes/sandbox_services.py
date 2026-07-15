from contextlib import nullcontext
from dataclasses import dataclass, field

from sandbox.services import ProcessResult


@dataclass
class _Recorder:
    name: str
    calls: list

    def _record(self, method, *args, **kwargs):
        self.calls.append((f"{self.name}.{method}", args, kwargs))


class RecordingProcessRunner(_Recorder):
    def run(self, argv, *, cwd=None, env=None, timeout=None):
        self._record("run", tuple(argv), cwd=cwd, env=env, timeout=timeout)
        return ProcessResult(tuple(argv), 0, "ok", "")


class RecordingHttpProbe(_Recorder):
    def probe(self, url, *, timeout=5):
        self._record("probe", url, timeout=timeout)
        return True


class RecordingPortAllocator(_Recorder):
    def allocate(self, preferred=None):
        self._record("allocate", preferred)
        return preferred or 8200

    def reserve(self, preferred=None):
        self._record("reserve", preferred)
        return nullcontext(type("Reservation", (), {"port": preferred or 8200})())


class RecordingPathPolicy(_Recorder):
    def require_allowed(self, path):
        self._record("require_allowed", path)
        return path

    def artifact_path(self, root, *parts):
        self._record("artifact_path", root, *parts)
        return "/".join((str(root), *(str(part) for part in parts)))


class RecordingProxyManager(_Recorder):
    def plan(self, hostname, port):
        self._record("plan", hostname, port)
        return {"hostname": hostname, "port": port}

    def apply(self, plan):
        self._record("apply", plan)

    def remove(self, hostname):
        self._record("remove", hostname)


@dataclass
class ServiceRecorders:
    calls: list = field(default_factory=list)

    def __post_init__(self):
        self.process = RecordingProcessRunner("process", self.calls)
        self.http = RecordingHttpProbe("http", self.calls)
        self.ports = RecordingPortAllocator("ports", self.calls)
        self.paths = RecordingPathPolicy("paths", self.calls)
        self.proxy = RecordingProxyManager("proxy", self.calls)
