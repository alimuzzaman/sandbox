"""Sandbox test package. Importing it guards the runner process; see runner_guard."""

from tests.runner_guard import install as _install_runner_guard

_install_runner_guard()
