"""Locate the WP-CLI command behind its leading global options."""
from __future__ import annotations

# WP-CLI global parameters that take a value, as --name=value or --name value.
_VALUE_OPTIONS = frozenset({
    "--path", "--url", "--ssh", "--http", "--user", "--require", "--exec", "--context",
})
_STAGING_COMMANDS = (
    ("plugin", "install"), ("theme", "install"), ("media", "import"), ("eval-file",),
)


def staging_command(argv) -> tuple[tuple[str, ...], int] | None:
    """Return (command, operand_start) if argv runs a host-file command, else None."""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            # Skip every delimiter: callers along the way may add or strip one,
            # and the answer must not depend on how many survive.
            index += 1
            continue
        if not token.startswith("-"):
            break
        index += 1
        if token in _VALUE_OPTIONS and index < len(argv) and not argv[index].startswith("-"):
            index += 1
    for command in _STAGING_COMMANDS:
        end = index + len(command)
        if tuple(argv[index:end]) == command:
            return command, end
    return None
