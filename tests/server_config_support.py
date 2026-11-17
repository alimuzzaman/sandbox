"""Shared fixtures for the server configuration tests."""
import shutil
import tempfile


def private_base(test) -> str:
    """A repository base whose parent the current user owns, mode 0700.

    The repository refuses a base whose parent is writable by others, as
    Linux ``/tmp`` (root, 1777) is; ``mkdtemp()`` alone puts the base right
    under it. The parent is removed when the test ends.
    """
    parent = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, parent, True)
    return tempfile.mkdtemp(dir=parent)
