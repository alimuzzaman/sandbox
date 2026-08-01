"""Sandbox-owned, non-forwarding dnsmasq authority lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterable

from .models import AnsweringAuthority, ResolutionBinding


class DnsmasqAuthority:
    def __init__(self, root: str | Path, *, process, binary: str,
                 pid_reader: Callable | None = None,
                 pid_matches: Callable | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.process = process
        self.binary = str(Path(binary))
        self.config_path = self.root / "dnsmasq.conf"
        self.pid_file = self.root / "dnsmasq.pid"
        self.log_file = self.root / "dnsmasq.log"
        self.state_path = self.root / "authority-state.json"
        self.pid_reader = pid_reader or self._read_pid
        self.pid_matches = pid_matches or self._pid_matches

    @staticmethod
    def render_config(*, address: str, port: int,
                      bindings: Iterable[ResolutionBinding],
                      pid_file: str, log_file: str) -> str:
        lines = [
            "# sandbox scoped authority v1", "no-resolv", "no-hosts", "no-poll",
            "domain-needed", "bogus-priv", "bind-interfaces", "local-service",
            f"listen-address={address}", f"port={port}",
            f"pid-file={pid_file}", f"log-facility={log_file}",
            "log-queries=extra",
        ]
        for binding in sorted(bindings, key=lambda item: (item.name, item.binding_id)):
            name = binding.name.removeprefix("*.")
            lines.append(f"address=/{name}/{binding.target}")
            lines.append(f"local=/{name}/")
        return "\n".join(lines) + "\n"

    def _atomic_write(self, path: Path, text: str, mode: int = 0o600) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {}
        value = json.loads(self.state_path.read_text())
        return value if isinstance(value, dict) else {}

    def _save(self, value: dict) -> None:
        self._atomic_write(
            self.state_path,
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        )

    def _read_pid(self, path: Path):
        try:
            pid = int(path.read_text().strip())
            start = Path(f"/proc/{pid}/stat").read_text().split()[21]
            return pid, start
        except (OSError, ValueError, IndexError):
            return None, None

    @staticmethod
    def _pid_matches(pid: int, identity: str) -> bool:
        try:
            return Path(f"/proc/{pid}/stat").read_text().split()[21] == identity
        except (OSError, IndexError):
            return False

    def ensure(self, bindings: Iterable[ResolutionBinding], *, address: str, port: int):
        bindings = tuple(bindings)
        text = self.render_config(
            address=address, port=port, bindings=bindings,
            pid_file=str(self.pid_file), log_file=str(self.log_file),
        )
        digest = hashlib.sha256(text.encode()).hexdigest()
        state = self._load()
        pid, pid_start = self.pid_reader(self.pid_file)
        if state.get("config_digest") == digest and pid and self.pid_matches(pid, pid_start):
            return AnsweringAuthority(
                "sandbox-dnsmasq", address, port, self.binary, str(self.config_path),
                tuple(item.binding_id for item in bindings), pid, pid_start,
                "healthy", digest,
            )
        if state.get("pid") and state.get("pid_start") and self.pid_matches(
            state["pid"], state["pid_start"],
        ):
            self.process.run(("kill", "-TERM", str(state["pid"])), timeout=5)
        self._atomic_write(self.config_path, text)
        result = self.process.run(
            (self.binary, "--conf-file", str(self.config_path)), timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "dnsmasq authority failed")[:1000])
        pid, pid_start = self.pid_reader(self.pid_file)
        state = {
            "address": address, "port": port, "config_digest": digest,
            "bindings": {item.binding_id: item.to_dict() for item in bindings},
            "pid": pid, "pid_start": pid_start,
        }
        self._save(state)
        return AnsweringAuthority(
            "sandbox-dnsmasq", address, port, self.binary, str(self.config_path),
            tuple(item.binding_id for item in bindings), pid, pid_start,
            "healthy" if pid and self.pid_matches(pid, pid_start) else "starting", digest,
        )

    def remove(self, binding_id: str) -> bool:
        state = self._load()
        bindings = state.get("bindings") or {}
        if binding_id not in bindings:
            return False
        del bindings[binding_id]
        if bindings:
            values = tuple(ResolutionBinding.from_dict(item) for item in bindings.values())
            self.ensure(values, address=state["address"], port=int(state["port"]))
            return True
        pid, identity = state.get("pid"), state.get("pid_start")
        if pid and identity and self.pid_matches(pid, identity):
            self.process.run(("kill", "-TERM", str(pid)), timeout=5)
        for path in (self.config_path, self.pid_file, self.state_path):
            try:
                if path.is_symlink():
                    raise RuntimeError(f"refusing to remove symlinked authority path: {path}")
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return True
