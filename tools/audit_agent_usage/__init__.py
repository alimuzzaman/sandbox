"""Audit-only synthetic agent-usage parser package.

Nothing in this package is registered with Sandbox.  The public entry points
accept explicit paths and operate only on local synthetic fixture files.
"""

from .parser import ParseResult, parse_jsonl, parse_to_directory, write_result
from .validator import ValidationResult, validate_output_files, validate_result


_COVERAGE_EXPORTS = frozenset(
    {
        "COVERAGE_SCHEMA",
        "reconcile_coverage",
        "reconcile_input",
        "reconcile_to_directory",
        "write_manifest",
    }
)


def __getattr__(name: str):
    """Load L1.3 lazily so ``python -m ...coverage`` stays warning-free."""

    if name in _COVERAGE_EXPORTS:
        from importlib import import_module

        module = import_module(".coverage", __name__)
        return getattr(module, name)
    raise AttributeError(name)

__all__ = [
    "ParseResult",
    "ValidationResult",
    "COVERAGE_SCHEMA",
    "parse_jsonl",
    "parse_to_directory",
    "reconcile_coverage",
    "reconcile_input",
    "reconcile_to_directory",
    "validate_output_files",
    "validate_result",
    "write_manifest",
    "write_result",
]
