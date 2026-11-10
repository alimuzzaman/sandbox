"""Temporary git repositories for controller auto-update tests (spec 072).

Layout under one temporary directory:

- ``origin.git``: bare repository standing in for the GitHub origin.
- ``dev``: a clone used to publish new commits to ``origin.git``.
- ``ctl``: the controller checkout under test. Its configured origin URL is
  a GitHub URL, mapped to ``origin.git`` with ``url.<base>.insteadOf`` so
  fetches stay local while the raw URL still reads as GitHub.

Git runs with an isolated global config so a developer's signing or URL
rewrite settings cannot leak into the fixture.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

GITHUB_URL = "https://github.com/example/sandbox.git"


def git_env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    config = home / "gitconfig"
    if not config.exists():
        config.write_text(
            "[user]\n\tname = Test\n\temail = test@example.test\n"
            "[init]\n\tdefaultBranch = main\n"
            "[commit]\n\tgpgsign = false\n"
            "[advice]\n\tdetachedHead = false\n",
            encoding="utf-8",
        )
    env.update({
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(home),
    })
    return env


class Repos:
    """A bare origin, a publishing clone and a controller clone."""

    def __init__(self, base: Path | None = None) -> None:
        if base is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="sb-ctl-update-")
            base = Path(self._tmp.name)
        else:
            self._tmp = None
        Path(base).mkdir(parents=True, exist_ok=True)
        self.base = Path(base).resolve()
        self.env = git_env(self.base)
        self.bare = self.base / "origin.git"
        self.dev = self.base / "dev"
        self.ctl = self.base / "ctl"
        self.dev.mkdir()
        self.git(self.dev, "init", "-q", "-b", "main")
        self.c1 = self.commit(self.dev, "a.txt", "one\n", "c1")
        self.git(self.base, "clone", "-q", "--bare", str(self.dev), str(self.bare))
        self.git(self.dev, "remote", "add", "origin", str(self.bare))
        self.git(self.dev, "fetch", "-q", "origin")
        self.git(self.dev, "branch", "-q", "-u", "origin/main", "main")
        self.git(self.base, "clone", "-q", str(self.bare), str(self.ctl))
        self.git(self.ctl, "remote", "set-url", "origin", GITHUB_URL)
        self.map_origin(self.bare)

    def process_env(self) -> dict[str, str]:
        """Variables to patch into ``os.environ`` so code under test, which
        builds its own git environment, reads the fixture's git config."""
        return {"GIT_CONFIG_GLOBAL": self.env["GIT_CONFIG_GLOBAL"], "GIT_CONFIG_NOSYSTEM": "1"}

    def cleanup(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------------

    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), env=self.env, capture_output=True,
            text=True, check=False, stdin=subprocess.DEVNULL, timeout=60,
        )
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def commit(self, repo: Path, name: str, content: str, message: str) -> str:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git(repo, "add", name)
        self.git(repo, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def publish(self, name: str = "b.txt", content: str = "two\n", message: str = "next",
                branch: str = "main") -> str:
        """Commit in ``dev`` on ``branch`` and push it to the bare origin."""
        current = self.git(self.dev, "rev-parse", "--abbrev-ref", "HEAD")
        if current != branch:
            self.git(self.dev, "checkout", "-q", "-B", branch)
        sha = self.commit(self.dev, name, content, message)
        self.git(self.dev, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        if current != branch:
            self.git(self.dev, "checkout", "-q", current)
        return sha

    def map_origin(self, target: Path) -> None:
        self.git(self.ctl, "config", f"url.file://{target}.insteadOf", GITHUB_URL)

    def unmap_origin(self) -> None:
        self.git(self.ctl, "config", "--unset", f"url.file://{self.bare}.insteadOf")

    def head(self, repo: Path | None = None) -> str:
        return self.git(repo or self.ctl, "rev-parse", "HEAD")

    def fetch_ctl(self) -> None:
        self.git(self.ctl, "fetch", "-q", "origin")
