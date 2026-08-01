"""NetworkManager adapter for its documented systemd-resolved mode."""

class NetworkManagerAdapter:
    def __init__(self, resolved_delegate) -> None:
        self.delegate = resolved_delegate

    def plan(self, suffix, address, port):
        return self.delegate.plan(suffix, address, port)

    def apply(self, plan):
        return self.delegate.apply(plan)

    def rollback(self, plan):
        return self.delegate.rollback(plan)
