"""Command registry: command modules register their name->handler here; cli builds dispatch from it."""
COMMANDS = {}

def register(mapping):
    """Register {command_name: handler} from a feature module."""
    COMMANDS.update(mapping)
