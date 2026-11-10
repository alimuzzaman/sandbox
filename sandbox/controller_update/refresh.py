"""Post-update refresh, run by the updated checkout's own code (spec 072, R7).

Contract: ``python -m sandbox.controller_update.refresh`` with the checkout
root as the working directory. Exit 0 means the checkout is ready to run;
anything else makes the old controller roll the update back. Later revisions
must keep this entry point.

It converges ``.cli-venv`` through the existing digest-stamped installer
(a no-op when ``requirements-cli.txt`` is unchanged) and then imports the CLI
module, so a revision that cannot even start is rolled back too.
"""
from __future__ import annotations

import sys


def main() -> int:
    from sandbox.core._config import _ensure_cli_requirements
    from sandbox.core._paths import CLI_VENV

    cli_python = CLI_VENV / "bin" / "python"
    if cli_python.exists():
        _ensure_cli_requirements(cli_python)
    import sandbox.cli  # noqa: F401  (import check of the new revision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
