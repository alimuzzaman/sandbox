"""Installation paths, independent from project state and personal defaults."""
from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class ManagedPaths:
    releases: Path = Path('/usr/local/lib/sandbox/releases')
    current: Path = Path('/usr/local/lib/sandbox/current')
    state: Path = Path('/var/lib/sandbox')
    config: Path = Path('/etc/sandbox')
    ephemeral: Path = Path('/run/sandbox')
    logs: Path = Path('/var/log/sandbox')
    cache: Path = Path('/var/cache/sandbox')
    backups: Path = Path('/var/backups/sandbox')
    admin_state: Path = Path('/var/lib/sandbox-installation')
    lease_root: Path = Path('/run/lock/sandbox-installation')


def selected_home(home=None) -> Path:
    if home is not None:
        return Path(home).expanduser().absolute()
    # Same personal selector precedence, without importing the core facade.
    raw = os.environ.get('SANDBOX_HOME')
    if not raw:
        try:
            candidate = (Path.home() / '.config/sandbox/home').read_text().strip()
        except OSError:
            candidate = ''
        raw = candidate if candidate and Path(candidate).is_absolute() else None
    return Path(raw or '~/sandbox').expanduser().absolute()
