"""Private CLI output helpers shared by command implementations."""

from __future__ import annotations

import io
import os
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout

from sandbox.services.redaction import StreamingRedactor


@contextmanager
def suppress_stdout():
    """Suppress Python helper output within a CLI operation."""
    with open(os.devnull, "w") as sink:
        with redirect_stdout(sink):
            yield


class _RedactingWriter(io.TextIOBase):
    """Text stream that redacts credential material before it reaches ``target``.

    Machine-readable commands still let human progress lines through, so an
    operator sees what is happening, but a progress line must never carry a
    credential the final JSON document redacts (an autologin URL, a token).
    """

    def __init__(self, target) -> None:
        self._target = target
        self._redactor = StreamingRedactor()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        chunk = self._redactor.feed(str(text).encode("utf-8", errors="replace"))
        if chunk:
            self._target.write(chunk.decode("utf-8", errors="replace"))
        return len(text)

    def flush(self) -> None:
        self._target.flush()

    def finish(self) -> None:
        tail = self._redactor.finish()
        if tail:
            self._target.write(tail.decode("utf-8", errors="replace"))
        self._target.flush()


@contextmanager
def redacted_output():
    """Redact credentials from Python stdout/stderr written inside the block."""
    out = _RedactingWriter(sys.stdout)
    err = _RedactingWriter(sys.stderr)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            yield
    finally:
        out.finish()
        err.finish()


@contextmanager
def stdout_to_stderr():
    """Send Python and child-process stdout to stderr for the block.

    Used by ``--json`` commands whose stdout must carry only the final
    document. File descriptor 1 is pointed at fd 2 so inherited child
    output moves too, then restored on exit.
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        saved = os.dup(1)
    except (AttributeError, OSError, ValueError):
        with redirect_stdout(sys.stderr):
            yield
        return
    try:
        os.dup2(2, 1)
        with redirect_stdout(sys.stderr):
            yield
    finally:
        try:
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
        os.dup2(saved, 1)
        os.close(saved)

