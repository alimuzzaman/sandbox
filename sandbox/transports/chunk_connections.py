"""Upload-owned SSH masters: serial authentication, reusable bounded channels.

Only setup may authenticate. Channel fallback is disabled with ProxyCommand=false.
ControlPersist bounds orphan lifetime to 60 idle seconds after controller death.
"""
from __future__ import annotations

import os
import queue
import subprocess
import tempfile
import time
from contextlib import contextmanager

from sandbox.core import _remote


class ConnectionFailure(RuntimeError):
    def __init__(self, cause, attempts):
        self.cause, self.attempts = cause, attempts
        super().__init__(cause)


class UploadConnections:
    def __init__(self, entry, streams, remaining, cancelled):
        self.base = _remote.ssh_command_args(entry, '', multiplex=False)
        self.streams, self.remaining, self.cancelled = streams, remaining, cancelled
        self.paths = []
        self.available = queue.Queue()
        self.directory = None

    def argv(self, path, *args, command=None):
        # OpenSSH uses the first supplied value. Put upload-owned settings before
        # user/config defaults; the explicit ProxyCommand forbids TCP fallback.
        options = ['-S', path, ('-oControlMaster=yes' if '-M' in args else '-oControlMaster=no'), '-oControlPersist=60',
                   '-oConnectionAttempts=1', '-oBatchMode=yes']
        if command is not None or '-O' in args:
            options += ['-oProxyCommand=false']
        return [self.base[0], *options, *args, *self.base[1:-2], self.base[-2],
                *([] if command is None else [command])]

    def operation(self, argv):
        # Never retain or print arbitrary SSH diagnostics (paths/credentials).
        with tempfile.TemporaryFile() as diagnostic:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=diagnostic, timeout=min(20, self.remaining()), check=False)
            diagnostic.seek(0)
            raw = diagnostic.read(4096).lower()
        cause = ('authentication_denied' if b'permission denied' in raw else
                 'connection_refused' if any(x in raw for x in
                     (b'connection closed', b'connection reset', b'connection refused', b'kex_exchange_identification')) else
                 'ssh_setup_failed')
        return result.returncode, cause

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='sb-chunk-ssh-')
        try:
            for index in range(self.streams):
                path = self.directory.name + '/' + str(index)
                for attempt in range(3):
                    if self.cancelled(): raise ConnectionFailure('controller_cancelled', attempt)
                    # Pacing includes failed authentications. All setup is serial;
                    # ssh -f returns only after authentication/backgrounding.
                    time.sleep(.25 if attempt == 0 else 2 ** (attempt - 1))
                    try:
                        status, cause = self.operation(self.argv(path, '-M', '-N', '-f'))
                    except subprocess.TimeoutExpired:
                        status, cause = 255, 'connection_timeout'
                    except OSError:
                        raise ConnectionFailure('ssh_client_unavailable',attempt+1) from None
                    if status != 0 and os.path.lexists(path):
                        self.paths.append(path)
                        raise ConnectionFailure('master_state_unknown',attempt+1)
                    if status == 0:
                        self.paths.append(path)
                        checked, cause = self.operation(self.argv(path, '-O', 'check'))
                        if checked == 0: break
                        raise ConnectionFailure('master_unavailable', attempt + 1)
                    if cause not in ('connection_refused', 'connection_timeout') or attempt == 2:
                        raise ConnectionFailure(cause, attempt + 1)
                self.available.put(path)
            return self
        except BaseException:
            self.__exit__(); raise

    @contextmanager
    def channel(self, command):
        path = self.available.get()
        try: yield self.argv(path, command=command)
        finally: self.available.put(path)

    def __exit__(self, *_args):
        for path in self.paths:
            try:
                subprocess.run(self.argv(path, '-O', 'exit'), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2, check=False)
            except (OSError, subprocess.TimeoutExpired): pass
        if self.directory: self.directory.cleanup()
        return False
