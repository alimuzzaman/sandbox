"""Read-only external/outside-platform declaration."""

class ExternalResolverAdapter:
    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.adoptable = False

    def apply(self, _plan):
        raise RuntimeError("external resolver adapter is read-only")
