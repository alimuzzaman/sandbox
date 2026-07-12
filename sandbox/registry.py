"""Backward-compatible command registry with explicit ownership metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class CommandSpec:
    name: str
    handler: Callable
    aliases: tuple[str, ...] = ()
    owner: str = "legacy"
    order: int = 100
    configure: Callable | None = None
    scope: str = "legacy"
    required_capability: str | None = None
    destructive: bool = False
    legacy_id: str | None = None


class CommandRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, CommandSpec] = {}
        self._names: dict[str, str] = {}

    def add(self, spec: CommandSpec) -> CommandSpec:
        if spec.name in self._names:
            raise ValueError(f"duplicate command: {spec.name}")
        for name in (spec.name, *spec.aliases):
            if name in self._names:
                raise ValueError(f"duplicate command or alias: {name}")
        self._specs[spec.name] = spec
        for name in (spec.name, *spec.aliases):
            self._names[name] = spec.name
        return spec

    def get(self, name: str) -> CommandSpec | None:
        canonical = self._names.get(name)
        return self._specs.get(canonical) if canonical else None

    def specs(self) -> tuple[CommandSpec, ...]:
        return tuple(sorted(self._specs.values(), key=lambda item: (item.order, item.name)))

    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.specs())


COMMANDS: dict[str, Callable] = {}
COMMAND_SPECS = CommandRegistry()


def register(mapping):
    """Register legacy mappings without changing the current dispatch contract."""
    for name, handler in mapping.items():
        COMMAND_SPECS.add(CommandSpec(
            name=name, handler=handler,
            owner=getattr(handler, "__module__", "legacy"),
            order=len(COMMANDS), legacy_id=name,
        ))
        COMMANDS[name] = handler


def register_specs(specs: Iterable[CommandSpec]) -> None:
    """Register feature-owned commands, including parser configuration."""
    for spec in specs:
        COMMAND_SPECS.add(spec)
        COMMANDS[spec.name] = spec.handler


def compose_missing_parsers(subparsers, specs: Iterable[CommandSpec]) -> tuple[str, ...]:
    """Add only feature-owned parsers absent from the bounded legacy parser."""
    added: list[str] = []
    existing = set(subparsers.choices or {})
    for spec in sorted(specs, key=lambda item: (item.order, item.name)):
        if spec.name in existing:
            continue
        parser = subparsers.add_parser(spec.name)
        if spec.configure is not None:
            spec.configure(parser)
        for alias in spec.aliases:
            if alias in existing:
                raise ValueError(f"duplicate command or alias: {alias}")
        existing.add(spec.name)
        existing.update(spec.aliases)
        added.append(spec.name)
    return tuple(added)
