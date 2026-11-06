"""Fail-closed local transport for the fixed zone coverage store program."""
import base64
import json
import shlex
import sys

from sandbox.hosting.zone_coverage import program
from tests.subprocess_support import run_test_process


def validate_store_command(command):
    try:
        parts = shlex.split(command)
        if (len(parts) != 5 or parts[:3] != ["python3", "-c", program.PROGRAM]
                or parts[3] != "${SANDBOX_HOME:-$HOME/sandbox}"):
            raise ValueError("unexpected command")
        request = json.loads(base64.b64decode(parts[4], validate=True))
        if not isinstance(request, dict) or program.remote_command(request) != command:
            raise ValueError("noncanonical command")
    except (ValueError, TypeError) as exc:
        raise AssertionError("unexpected store transport command") from exc
    return parts[4]


_active_guard = None


def store_transport(home, guard=None):
    def run(entry, command, timeout=15):
        payload = validate_store_command(command)
        argv = [sys.executable, "-I", "-B", "-c", program.PROGRAM, home, payload]
        active = guard or _active_guard
        if active is not None:
            active.allow_command(argv)
        return run_test_process(
            argv,
            env={"SANDBOX_HOME": home, "HOME": home},
            capture_output=True, text=True, timeout=timeout)
    return run

# A removable process-local boundary for these integration fixtures.  Callers
# explicitly register complete argument vectors and owned listening endpoints.
import contextlib
import ipaddress
import os
from pathlib import Path
import tempfile
import socket
import subprocess
import urllib.request
from unittest import mock


class GuardedIO:
    def __init__(self):
        self.commands = set()
        self.git_roots = set()
        self.endpoints = set()
        self.attempts = []
        self.stack = contextlib.ExitStack()

    def allow_command(self, argv):
        argv = tuple(argv)
        root = Path(tempfile.gettempdir()).resolve()
        def local(path):
            return Path(path).resolve().is_relative_to(root)
        store = (len(argv) == 7 and argv[:5] == (sys.executable, "-I", "-B", "-c", program.PROGRAM)
                 and local(argv[5]))
        if store:
            try:
                request = json.loads(base64.b64decode(argv[6], validate=True))
                store = isinstance(request, dict) and bool(validate_store_command(program.remote_command(request)))
            except (TypeError, ValueError, AssertionError):
                store = False
        git = (len(argv) >= 5 and argv[:2] == ("git", "-C") and local(argv[2])
               and argv[3:] in (("init", "--initial-branch=main"), ("add", "."),
                                ("commit", "-m", "Synthetic hosting source"), ("rev-parse", "HEAD")))
        openssl = (len(argv) == 14 and argv[:6] == ("openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout")
                   and local(argv[6]) and argv[7] == "-out" and local(argv[8])
                   and argv[9:13] == ("-days", "1", "-nodes", "-subj")
                   and argv[13].startswith("/CN="))
        if not (store or git or openssl):
            raise AssertionError("unapproved process registration")
        self.commands.add(argv)
        if git and argv[3:] == ("init", "--initial-branch=main"):
            self.git_roots.add(Path(argv[2]).resolve())

    def allow_loopback(self, host, port):
        if not ipaddress.ip_address(host).is_loopback or not 0 < port < 65536:
            raise AssertionError("endpoint must be owned loopback")
        self.endpoints.add((host, port))

    def reject(self, *args, **kwargs):
        self.attempts.append((args, kwargs))
        raise AssertionError("unexpected external transport")

    def __enter__(self):
        global _active_guard
        if _active_guard is not None:
            raise AssertionError("nested guard")
        _active_guard = self
        from sandbox.core import _remote
        import sys as _sys
        original_popen = subprocess.Popen
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_getaddrinfo = socket.getaddrinfo
        aliases = {_remote.ssh_run, _remote.ssh_stream, _remote.remote_host_memory_request,
                   _remote.ssh_run_bounded, _remote.ssh_run_batch}
        for module in list(_sys.modules.values()):
            if module is None or not getattr(module, '__name__', '').startswith('sandbox.'):
                continue
            for name, value in list(vars(module).items()):
                if any(value is alias for alias in aliases):
                    self.stack.enter_context(mock.patch.object(module, name, self.reject))
        def popen(argv, *args, **kwargs):
            if (isinstance(argv, (tuple, list)) and len(argv) == 5
                    and tuple(argv[:2]) == ("git", "-C")
                    and tuple(argv[3:]) == ("rev-parse", "--show-toplevel")
                    and any(Path(argv[2]).resolve().is_relative_to(root) for root in self.git_roots)):
                self.commands.add(tuple(argv))
            if (kwargs.get('shell') or kwargs.get('executable') is not None
                    or not isinstance(argv, (tuple, list))
                    or tuple(argv) not in self.commands):
                return self.reject(argv)
            return original_popen(argv, *args, **kwargs)
        def connect(sock, address):
            if not isinstance(address, tuple) or address[:2] not in self.endpoints:
                return self.reject(address)
            return original_connect(sock, address)
        def connect_ex(sock, address):
            if not isinstance(address, tuple) or address[:2] not in self.endpoints:
                return self.reject(address)
            return original_connect_ex(sock, address)
        self.stack.enter_context(mock.patch.object(subprocess, 'Popen', popen))
        def getaddrinfo(host, port, *args, **kwargs):
            if (host, port) not in self.endpoints:
                return self.reject(host, port)
            return original_getaddrinfo(host, port, *args, **kwargs)
        self.stack.enter_context(mock.patch.object(socket, 'getaddrinfo', getaddrinfo))
        self.stack.enter_context(mock.patch.object(socket, 'gethostbyname', self.reject))
        self.stack.enter_context(mock.patch.object(socket, 'gethostbyname_ex', self.reject))
        self.stack.enter_context(mock.patch.object(socket.socket, 'connect', connect))
        self.stack.enter_context(mock.patch.object(socket.socket, 'connect_ex', connect_ex))
        self.stack.enter_context(mock.patch.object(urllib.request, 'urlopen', self.reject))
        return self

    def __exit__(self, *exc):
        global _active_guard
        try:
            return self.stack.__exit__(*exc)
        finally:
            _active_guard = None
