"""Private CLI output helpers shared by command implementations."""

from __future__ import annotations

import os
from contextlib import contextmanager, redirect_stdout


@contextmanager
def suppress_stdout():
    """Suppress Python helper output within a CLI operation."""
    with open(os.devnull, "w") as sink:
        with redirect_stdout(sink):
            yield
