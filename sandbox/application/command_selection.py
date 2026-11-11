"""Read the actual installed CLI grammar without dispatch, output or effects."""
import argparse
import copy
from contextvars import ContextVar
from dataclasses import dataclass


class CommandSelectionUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class ParsedCommandSelection:
    command: tuple[str | None, str | None]
    stdin_requested: bool


_SELECTOR = ContextVar('sandbox_cli_command_selector', default=None)


def bind_cli_command_parser(parser, normalize):
    """CLI composition supplies its completed real parser, never a client claim."""
    return _SELECTOR.set((parser, normalize))


def reset_cli_command_parser(token):
    """Restore an embedding's previous parser after its invocation ends."""
    _SELECTOR.reset(token)


def selected_command(argv):
    selection = selected_invocation(argv)
    return selection.command if selection is not None else None


def selected_invocation(argv):
    selected = _SELECTOR.get()
    if selected is None:
        return None
    original, normalize = selected
    parser = copy.deepcopy(original)
    visited = set()
    def refuse(*args, **kwargs):
        raise CommandSelectionUnavailable('retained CLI command does not match the installed grammar')
    def quiet(item):
        if id(item) in visited:
            return
        visited.add(id(item))
        item._print_message = lambda *args, **kwargs: None
        item.exit = refuse
        for action in item._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    quiet(child)
    quiet(parser)
    try:
        args = parser.parse_args(normalize(list(argv)))
    except (argparse.ArgumentError, SystemExit):
        raise CommandSelectionUnavailable('retained CLI command does not match the installed grammar') from None
    # Use the installed parser's resolved destinations. Argparse accepts legal
    # abbreviations, so the original token spelling is not an input-mode fact.
    stdin_requested = any(value is True and (name == 'stdin' or name.endswith('_stdin')
                                             or name == 'interactive')
                          for name, value in vars(args).items())
    stdin_requested = stdin_requested or getattr(args, 'file', None) == '-'
    return ParsedCommandSelection(
        (getattr(args, 'cmd', None), getattr(args, 'action', None)), stdin_requested)
