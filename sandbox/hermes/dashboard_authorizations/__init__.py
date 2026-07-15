"""Register the dashboard-only authorization plugin without agent tools."""


def register(_ctx) -> None:
    """Hermes requires user-installed dashboard plugins to be explicitly enabled."""
