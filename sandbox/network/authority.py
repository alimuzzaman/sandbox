"""Sandbox-owned, non-forwarding dnsmasq authority lifecycle."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import stat
import time
from typing import Callable, Iterable

from .models import AnsweringAuthority, ResolutionBinding


class DnsmasqAuthority:
    def __init__(self, root: str | Path, *, process, binary: str,
                 pid_reader: Callable | None = None,
                 pid_matches: Callable | None = None,
                 pid_executable: Callable | None = None,
                 listener_matches: Callable | None = None,
                 sleeper: Callable[[float], None] | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.process = process
        self.binary = str(Path(binary))
        self.config_path = self.root / "dnsmasq.conf"
        self.pid_file = self.root / "dnsmasq.pid"
        self.log_file = self.root / "dnsmasq.log"
        self.state_path = self.root / "authority-state.json"
        self.lock_path = self.root / "authority.lock"
        self.pid_reader = pid_reader or self._read_pid
        self.pid_matches = pid_matches or self._pid_matches
        self.pid_executable = pid_executable or self._pid_executable
        self.listener_matches = listener_matches or self._listener_matches
        self.sleeper = sleeper or time.sleep

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
            if binding.kind == "zone":
                lines.append(f"address=/{name}/{binding.target}")
                lines.append(f"local=/{name}/")
            else:
                lines.append(f"host-record={name},{binding.target}")
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
        payload = self._owned_file_bytes(self.state_path)
        if payload is None:
            return {}
        value = json.loads(payload.decode("utf-8"))
        return value if isinstance(value, dict) else {}

    def _owned_file_bytes(self, path: Path) -> bytes | None:
        try:
            descriptor = os.open(
                path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RuntimeError(f"unsafe authority path: {path}") from exc
        try:
            details = os.fstat(descriptor)
            if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1
                    or details.st_uid != os.getuid() or details.st_mode & 0o077):
                raise RuntimeError(f"unsafe authority path: {path}")
            chunks = []
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(descriptor)

    def _owned_config(self, state: dict) -> str | None:
        payload = self._owned_file_bytes(self.config_path)
        expected = state.get("config_digest")
        if not expected:
            if payload is not None:
                raise RuntimeError("foreign authority config exists without an ownership receipt")
            return None
        if payload is None:
            raise RuntimeError("owned authority config is missing")
        if hashlib.sha256(payload).hexdigest() != expected:
            raise RuntimeError("owned authority config drifted")
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("owned authority config is not valid UTF-8") from exc

    def _save(self, value: dict) -> None:
        self._atomic_write(
            self.state_path,
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        )

    @contextmanager
    def _locked(self):
        descriptor = os.open(
            self.lock_path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            details = os.fstat(descriptor)
            if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1
                    or details.st_uid != os.getuid() or details.st_mode & 0o077):
                raise RuntimeError("authority lock ownership is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

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

    def _pid_executable(self, pid: int) -> bool:
        try:
            return Path(f"/proc/{pid}/exe").resolve() == Path(self.binary).resolve()
        except OSError:
            return False

    def _listener_matches(self, pid: int, address: str, port: int) -> bool:
        try:
            result = self.process.run(("ss", "-H", "-lntup"), timeout=2)
        except (OSError, TypeError):
            return None
        if result.returncode != 0:
            return None
        endpoint = f"{address}:{int(port)}"
        lines = [line for line in (result.stdout or "").splitlines()
                 if endpoint in line and f"pid={pid}," in line]
        return any(line.startswith("tcp") for line in lines) and any(
            line.startswith("udp") for line in lines
        )

    def _healthy(self, state: dict, pid, pid_start) -> bool:
        return bool(self._process_owned(state, pid, pid_start)
                    and self.listener_matches(
                        pid, state.get("address"), int(state.get("port", 0)),
                    ))

    def _process_owned(self, state: dict, pid, pid_start) -> bool:
        return bool(
            pid and pid_start and state.get("pid") == pid
            and state.get("pid_start") == pid_start
            and self.pid_matches(pid, pid_start)
            and self.pid_executable(pid)
        )

    def ensure(self, bindings: Iterable[ResolutionBinding], *, address: str, port: int,
               reservation=None):
        with self._locked():
            return self._ensure(
                bindings, address=address, port=port, reservation=reservation,
            )

    def _ensure(self, bindings: Iterable[ResolutionBinding], *, address: str, port: int,
                reservation=None):
        bindings = tuple(bindings)
        text = self.render_config(
            address=address, port=port, bindings=bindings,
            pid_file=str(self.pid_file), log_file=str(self.log_file),
        )
        digest = hashlib.sha256(text.encode()).hexdigest()
        state = self._load()
        previous_config = self._owned_config(state)
        owned = state.get("bindings") or {}
        if owned and (
            state.get("address") != address or int(state.get("port", 0)) != int(port)
        ):
            raise RuntimeError(
                "refusing to move an active shared DNS authority endpoint"
            )
        pid, pid_start = self.pid_reader(self.pid_file)
        if state.get("config_digest") == digest and self._healthy(state, pid, pid_start):
            return AnsweringAuthority(
                "sandbox-dnsmasq", address, port, self.binary, str(self.config_path),
                tuple(item.binding_id for item in bindings), pid, pid_start,
                "healthy", digest,
            )
        if owned and not self._process_owned(state, pid, pid_start):
            # Distinguish "our process is gone" from "someone else holds it".
            # A recorded process that no longer exists is a stale record — after
            # a reboot, an OOM kill, or an operator stopping it — and refusing
            # there left the authority permanently unstartable with no verb to
            # reset it. Only a LIVE process we cannot prove is ours is drift.
            recorded = state.get("pid")
            live_foreign = bool(
                recorded and pid and int(recorded) == int(pid)
                and (self.pid_matches(pid, pid_start) or self.pid_executable(pid))
            )
            if live_foreign:
                raise RuntimeError("owned authority process identity drifted")
            state = {**state, "pid": None, "pid_start": None}
        self._atomic_write(self.config_path, text)
        # dnsmasq only accepts the equals form for long options; a separate
        # argument is rejected with "junk found in command line", which read as
        # an endpoint collision and blocked every adoption on a real host.
        checked = self.process.run(
            (self.binary, "--test", f"--conf-file={self.config_path}"), timeout=10,
        )
        if checked.returncode != 0:
            if previous_config is None:
                self.config_path.unlink(missing_ok=True)
            else:
                self._atomic_write(self.config_path, previous_config)
            raise RuntimeError((checked.stderr or "dnsmasq authority config rejected")[:1000])
        if reservation is not None:
            if reservation.address != address or int(reservation.port) != int(port):
                raise RuntimeError("DNS endpoint reservation does not match the authority plan")
            reservation.release()
            reservation = None
        if self._process_owned(state, pid, pid_start):
            self.process.run(("kill", "-TERM", str(state["pid"])), timeout=5)
        result = self.process.run(
            (self.binary, f"--conf-file={self.config_path}"), timeout=10,
        )
        if result.returncode != 0:
            if previous_config is None:
                self.config_path.unlink(missing_ok=True)
            else:
                self._atomic_write(self.config_path, previous_config)
                restored = self.process.run(
                    (self.binary, f"--conf-file={self.config_path}"), timeout=10,
                )
                if restored.returncode == 0:
                    restored_pid, restored_start = self.pid_reader(self.pid_file)
                    state.update({"pid": restored_pid, "pid_start": restored_start})
                    self._save(state)
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
            "healthy" if self._healthy(state, pid, pid_start) else "starting", digest,
        )

    def _forget(self, state: dict) -> None:
        """Drop generated artifacts and state for an authority that is not running."""
        for path in (self.config_path, self.pid_file, self.state_path):
            try:
                if path.is_symlink():
                    continue
                path.unlink(missing_ok=True)
            except OSError:
                continue

    def remove(self, binding_id: str) -> bool:
        with self._locked():
            state = self._load()
            bindings = state.get("bindings") or {}
            if binding_id not in bindings:
                return False
            del bindings[binding_id]
            if bindings:
                values = tuple(ResolutionBinding.from_dict(item) for item in bindings.values())
                self._ensure(values, address=state["address"], port=int(state["port"]))
                return True
            self._owned_config(state)
            pid, identity = state.get("pid"), state.get("pid_start")
            if not self._process_owned(state, pid, identity):
                # The recorded process is gone -- killed, OOMed, or lost to a
                # reboot. Its final owned zone is being removed anyway, so the
                # goal state is already reached; refusing here reported cleanup
                # as incomplete forever with nothing left to clean.
                current, _ = self.pid_reader(self.pid_file)
                if current and self.pid_executable(current):
                    # Something IS running under that pid; identity drift there
                    # is a preservation case, not a cleanup case.
                    return False
                self._forget(state)
                return True
            terminated = self.process.run(("kill", "-TERM", str(pid)), timeout=5)
            if terminated.returncode != 0:
                return False
            for _attempt in range(20):
                still_owned = self.pid_matches(pid, identity)
                still_listening = self.listener_matches(
                    pid, state.get("address"), int(state.get("port", 0)),
                )
                if not still_owned and still_listening is False:
                    break
                self.sleeper(0.05)
            else:
                return False
            for path in (self.config_path, self.pid_file, self.state_path):
                if os.path.lexists(path) and path.is_symlink():
                    return False
            for path in (self.config_path, self.pid_file, self.state_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    return False
            return True

    def status(self) -> dict:
        state = self._load()
        pid, identity = state.get("pid"), state.get("pid_start")
        healthy = self._healthy(state, pid, identity)
        return {
            "health": "healthy" if healthy else "unhealthy",
            "address": state.get("address"), "port": state.get("port"),
            "config_digest": state.get("config_digest"),
            "bindings": sorted((state.get("bindings") or {}).keys()),
        }
