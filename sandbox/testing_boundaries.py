"""Static enforcement for safe test subprocess and environment handling."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestBoundaryViolation:
    file: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.rule}"


def _is_parent_environment(
    node: ast.AST, aliases: set[str], os_modules: set[str],
) -> bool:
    return (
        isinstance(node, ast.Name) and node.id in aliases
    ) or (
        isinstance(node, ast.Attribute) and node.attr == "environ"
        and isinstance(node.value, ast.Name) and node.value.id in os_modules
    )


def _parent_environment_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    os_modules = {"os"}
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name == "os":
                    os_modules.add(imported.asname or imported.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for imported in node.names:
                if imported.name == "environ":
                    aliases.add(imported.asname or imported.name)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if value is None or not _is_parent_environment(
                value, aliases, os_modules,
            ):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
    return aliases, os_modules


def _iterates_parent_environment(
    node: ast.AST, aliases: set[str], os_modules: set[str],
) -> bool:
    if _is_parent_environment(node, aliases, os_modules):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"items", "keys", "values"}
        and _is_parent_environment(node.func.value, aliases, os_modules)
    )


def _bulk_parent_environment_call(
    node: ast.Call, aliases: set[str], os_modules: set[str],
) -> str | None:
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"items", "keys", "values"}
        and _is_parent_environment(node.func.value, aliases, os_modules)
    ):
        return "parent-env-iteration"
    if (
        isinstance(node.func, ast.Attribute) and node.func.attr == "copy"
        and _is_parent_environment(node.func.value, aliases, os_modules)
    ):
        return "parent-env-copy"
    if (
        isinstance(node.func, ast.Name)
        and node.func.id in {"dict", "list", "tuple", "set"}
        and node.args
        and _iterates_parent_environment(node.args[0], aliases, os_modules)
    ):
        return "parent-env-materialize"
    if (
        isinstance(node.func, ast.Attribute) and node.func.attr == "update"
        and node.args
        and _iterates_parent_environment(node.args[0], aliases, os_modules)
    ):
        return "parent-env-update"
    if any(
        keyword.arg == "env"
        and _is_parent_environment(keyword.value, aliases, os_modules)
        for keyword in node.keywords
    ):
        return "parent-env-direct"
    return None


def _iteration_expression(node: ast.AST) -> ast.AST | None:
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return node.iter
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        for generator in node.generators:
            return generator.iter
    return None


def _parent_environment_unpack(
    node: ast.Dict, aliases: set[str], os_modules: set[str],
) -> bool:
    return any(
        key is None and _is_parent_environment(value, aliases, os_modules)
        for key, value in zip(node.keys, node.values)
    )


def _parent_environment_union(
    node: ast.BinOp, aliases: set[str], os_modules: set[str],
) -> bool:
    return isinstance(node.op, ast.BitOr) and (
        _is_parent_environment(node.left, aliases, os_modules)
        or _is_parent_environment(node.right, aliases, os_modules)
    )


def inspect_test_environment_boundaries(
    tests_root: Path,
) -> tuple[TestBoundaryViolation, ...]:
    violations: list[TestBoundaryViolation] = []
    for path in sorted(tests_root.rglob("*.py")):
        relative = str(path.relative_to(tests_root))
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:
            violations.append(TestBoundaryViolation(
                relative, exc.lineno or 0, "syntax-error",
            ))
            continue
        aliases, os_modules = _parent_environment_names(tree)
        for node in ast.walk(tree):
            rule = None
            if isinstance(node, ast.Dict) and _parent_environment_unpack(
                node, aliases, os_modules,
            ):
                rule = "parent-env-unpack"
            elif isinstance(node, ast.BinOp) and _parent_environment_union(
                node, aliases, os_modules,
            ):
                rule = "parent-env-union"
            elif isinstance(node, ast.Call):
                rule = _bulk_parent_environment_call(node, aliases, os_modules)
            else:
                expression = _iteration_expression(node)
                if expression is not None and _iterates_parent_environment(
                    expression, aliases, os_modules,
                ):
                    rule = "parent-env-iteration"
            if rule is not None:
                violations.append(TestBoundaryViolation(relative, node.lineno, rule))
    return tuple(violations)
