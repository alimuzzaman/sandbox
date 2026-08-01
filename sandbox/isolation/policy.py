"""Compile canonical mount visibility; reject symlink and parent escapes."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


FORBIDDEN_TARGETS = ("/proc", "/sys", "/dev", "/run/host", "/run/systemd")


class MountPolicyCompiler:
    def __init__(self, *, allowed_sources):
        self.allowed_sources = tuple(Path(item).expanduser().resolve() for item in allowed_sources)

    @staticmethod
    def _target(value):
        target = PurePosixPath(value)
        if not target.is_absolute() or ".." in target.parts:
            raise ValueError("container mount target is invalid")
        text = str(target)
        if any(text == root or text.startswith(root + "/") for root in FORBIDDEN_TARGETS):
            raise ValueError("container mount target exposes a control surface")
        return text

    def _source(self, value):
        source = Path(value).expanduser()
        current = Path(source.anchor)
        for part in source.parts[1:]:
            current /= part
            if current.is_symlink():
                raise ValueError("mount source contains a symlink")
        resolved = source.resolve(strict=True)
        if not any(resolved == root or resolved.is_relative_to(root)
                   for root in self.allowed_sources):
            raise ValueError("mount source is outside allowed roots")
        return str(resolved)

    def compile(self, *, read_only=(), writable=()):
        ro = tuple({"source": self._source(item["source"]),
                    "target": self._target(item["target"]), "mode": "ro"}
                   for item in read_only)
        rw = tuple({"source": self._source(item["source"]),
                    "target": self._target(item["target"]), "mode": "rw"}
                   for item in writable)
        targets = [item["target"] for item in (*ro, *rw)]
        if len(targets) != len(set(targets)):
            raise ValueError("mount target is declared more than once")
        return {"read_only": ro, "writable": rw}
