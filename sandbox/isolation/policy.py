"""Compile canonical mount visibility; reject symlink and parent escapes."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


FORBIDDEN_TARGETS = ("/proc", "/sys", "/dev", "/run/host", "/run/systemd")


class MountPolicyCompiler:
    def __init__(self, *, allowed_sources, writable_sources=()):
        self._allowed_entries = tuple(
            (Path(item).expanduser().absolute(), Path(item).expanduser().resolve())
            for item in allowed_sources
        )
        self.allowed_sources = tuple(resolved for _declared, resolved in self._allowed_entries)
        self.writable_sources = tuple(Path(item).expanduser().resolve() for item in writable_sources)

    @staticmethod
    def _target(value):
        target = PurePosixPath(value)
        if not target.is_absolute() or ".." in target.parts:
            raise ValueError("container mount target is invalid")
        text = str(target)
        if any(text == root or text.startswith(root + "/") for root in FORBIDDEN_TARGETS):
            raise ValueError("container mount target exposes a control surface")
        return text

    def _source(self, value, *, writable=False):
        source = Path(value).expanduser().absolute()
        resolved = source.resolve(strict=True)
        allowed = next((entry for entry in self._allowed_entries
                        if resolved == entry[1] or resolved.is_relative_to(entry[1])), None)
        if allowed is None:
            raise ValueError("mount source is outside allowed roots")

        declared_root, canonical_root = allowed
        try:
            relative = source.relative_to(declared_root)
            current = declared_root
        except ValueError:
            relative = resolved.relative_to(canonical_root)
            current = canonical_root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("mount source contains a symlink")
        if writable and not any(resolved == root or resolved.is_relative_to(root)
                                for root in self.writable_sources):
            raise ValueError("writable mount source is outside explicit writable roots")
        return str(resolved)

    def compile(self, *, read_only=(), writable=()):
        ro = tuple({"source": self._source(item["source"]),
                    "target": self._target(item["target"]), "mode": "ro"}
                   for item in read_only)
        rw = tuple({"source": self._source(item["source"], writable=True),
                    "target": self._target(item["target"]), "mode": "rw"}
                   for item in writable)
        targets = [item["target"] for item in (*ro, *rw)]
        if len(targets) != len(set(targets)):
            raise ValueError("mount target is declared more than once")
        return {"read_only": ro, "writable": rw}
