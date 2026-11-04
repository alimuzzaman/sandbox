"""Locate the WP-CLI command behind its leading global options."""
from __future__ import annotations

# WP-CLI global parameters that take a value, as --name=value or --name value.
_VALUE_OPTIONS = frozenset({
    "--path", "--url", "--ssh", "--http", "--user", "--require", "--exec", "--context",
})
_STAGING_COMMANDS = (
    ("plugin", "install"), ("theme", "install"), ("media", "import"), ("eval-file",),
)


def _walk_leading_global_options(argv) -> int:
    """Return index of the first token after leading global options."""
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
    return index


def staging_command(argv) -> tuple[tuple[str, ...], int] | None:
    """Return (command, operand_start) if argv runs a host-file command, else None."""
    index = _walk_leading_global_options(argv)
    for command in _STAGING_COMMANDS:
        end = index + len(command)
        if tuple(argv[index:end]) == command:
            return command, end
    return None


_FILE_STDIN_COMMANDS = frozenset({
    ("eval-file",),
    ("db", "import"),
    ("post", "create"),
    ("post", "update"),
})

_MIN_OPERANDS_COMMANDS = {
    ("db", "query"): 1,
    ("option", "add"): 2,
    ("option", "update"): 2,
    ("post", "meta", "add"): 3,
    ("post", "meta", "update"): 3,
    ("user", "meta", "add"): 3,
    ("user", "meta", "update"): 3,
    ("term", "meta", "add"): 3,
    ("term", "meta", "update"): 3,
    ("comment", "meta", "add"): 3,
    ("comment", "meta", "update"): 3,
}


def requires_stdin(argv) -> bool:
    """Return True if argv represents a WP-CLI command that reads from stdin."""
    if not argv:
        return False
    index = _walk_leading_global_options(argv)
    if index >= len(argv):
        return False

    matched_cmd = None
    cmd_end = index
    for length in (3, 2, 1):
        if index + length <= len(argv):
            candidate = tuple(argv[index:index + length])
            if candidate in _FILE_STDIN_COMMANDS or candidate in _MIN_OPERANDS_COMMANDS:
                matched_cmd = candidate
                cmd_end = index + length
                break

    if matched_cmd is None:
        return False

    operands = [
        argv[i] for i in range(cmd_end, len(argv))
        # WP-CLI options always start with `--`, so `-` and `-1` are positional values.
        if not argv[i].startswith("--")
    ]

    if matched_cmd == ("eval-file",):
        return bool(operands and operands[0] == "-")

    if matched_cmd in _FILE_STDIN_COMMANDS:
        return "-" in operands

    if matched_cmd in _MIN_OPERANDS_COMMANDS:
        return len(operands) < _MIN_OPERANDS_COMMANDS[matched_cmd]

    return False


def is_eval_file_stdin(argv) -> bool:
    """Return True if argv runs eval-file reading from stdin."""
    if not argv:
        return False
    index = _walk_leading_global_options(argv)
    if index < len(argv) and tuple(argv[index:index + 1]) == ("eval-file",):
        operands = [
            argv[i] for i in range(index + 1, len(argv))
            # WP-CLI options always start with `--`, so `-` and `-1` are positional values.
        if not argv[i].startswith("--")
        ]
        return bool(operands and operands[0] == "-")
    return False
