"""Read the actual installed CLI grammar without dispatch, output or effects."""
import argparse
import copy
from contextvars import ContextVar


class CommandSelectionUnavailable(ValueError):
    pass


_SELECTOR = ContextVar('sandbox_cli_command_selector', default=None)


def bind_cli_command_parser(parser, normalize):
    """CLI composition supplies its completed real parser, never a client claim."""
    return _SELECTOR.set((parser, normalize))


def reset_cli_command_parser(token):
    """Restore an embedding's previous parser after its invocation ends."""
    _SELECTOR.reset(token)


def selected_command(argv):
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
    return (getattr(args, 'cmd', None), getattr(args, 'action', None))
