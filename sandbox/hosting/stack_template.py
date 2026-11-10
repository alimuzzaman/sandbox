"""Stack templates: one hosting manifest rendered per slug (spec 071).

A template is a ``sandbox.hosting.yml`` with a top-level ``stack:`` block. Its
placeholders are substituted only in a fixed set of fields; everything else is
copied verbatim. The rendered result drops ``stack:``, gains an absolute
``source_root`` and a ``stack_instance`` block, and is then validated exactly
like a hand-written manifest. This module is pure: no Git, SSH or disk writes.
"""
from __future__ import annotations

import copy
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sandbox.core._hosting import HostingRefusal

TEMPLATE_REQUIRED = "stack_template_requires_stack_up"
TEMPLATE_INVALID = "stack_template_invalid"
SLUG_INVALID = "stack_slug_invalid"
BRANCH_UNAVAILABLE = "stack_branch_unavailable"

STACK_KEY = "stack"
INSTANCE_KEY = "stack_instance"
MANIFEST_NAME = "sandbox.hosting.yml"

# Every slug must also be safe as one path segment and one DNS label part,
# whatever the template's own pattern allows.
_SAFE_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_PORT_NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,30}")
_BRACE_RE = re.compile(r"\{([^{}]*)\}")
# Placeholder-shaped tokens; a plain manifest containing one is a template.
_PLACEHOLDER_RE = re.compile(r"\{(?:slug|SLUG|ref_branch|port:[^{}]*)\}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")

# Paths (relative to the manifest root) whose string values may hold
# placeholders. ``*`` matches one mapping key or list index.
_ALLOWED_PATHS = (
    ("project",),
    ("environments", "*", "host", "primary"),
    ("environments", "*", "host", "aliases", "*", "hostname"),
    ("environments", "*", "deploy", "allowed_branches", "*"),
    ("environments", "*", "secrets", "values", "*"),
    ("environments", "*", "secrets", "generated", "*"),
)
# Basic-auth route parameters (``/api/{id}``) are an existing brace syntax that
# its own validator owns; the template scan leaves that subtree alone.
_EXEMPT_PREFIXES = (("environments", "*", "basic_auth"),)


class StackTemplateError(HostingRefusal):
    """A template, slug or render that cannot become a valid manifest."""


@dataclass(frozen=True)
class StackRef:
    ref: str
    commit: str
    ref_branch: str  # "" for a detached commit

    def __post_init__(self) -> None:
        if not _COMMIT_RE.fullmatch(self.commit):
            raise ValueError("StackRef.commit must be a full lowercase commit")


@dataclass(frozen=True)
class StackTemplate:
    slug_pattern: re.Pattern
    ports: tuple[str, ...]
    raw: dict = field(repr=False)  # the manifest without ``stack:``

    def environment_names(self) -> tuple[str, ...]:
        environments = self.raw.get("environments")
        if not isinstance(environments, dict):
            return ()
        return tuple(sorted(str(name) for name in environments))


def manifest_kind(manifest: dict) -> str:
    """Return ``template``, ``rendered`` or ``plain`` for a loaded manifest."""
    if STACK_KEY in manifest:
        return "template"
    if _contains_placeholder(manifest):
        return "template"
    if INSTANCE_KEY in manifest:
        return "rendered"
    return "plain"


def refuse_plain_use() -> StackTemplateError:
    return StackTemplateError(
        TEMPLATE_REQUIRED,
        "this manifest is a stack template (it has a stack: block or a stack placeholder); "
        "deploy it with `sb host stack up --template <path> --name <slug> --remote <remote>`",
    )


def _contains_placeholder(node: object) -> bool:
    if isinstance(node, str):
        return bool(_PLACEHOLDER_RE.search(node))
    if isinstance(node, dict):
        return any(_contains_placeholder(key) or _contains_placeholder(value)
                   for key, value in node.items() if not str(key).startswith("_"))
    if isinstance(node, list):
        return any(_contains_placeholder(item) for item in node)
    return False


def _matches(path: tuple, pattern: tuple) -> bool:
    return len(path) == len(pattern) and all(
        expected == "*" or str(actual) == expected for actual, expected in zip(path, pattern))


def _is_allowed(path: tuple) -> bool:
    return any(_matches(path, pattern) for pattern in _ALLOWED_PATHS)


def _is_exempt(path: tuple) -> bool:
    return any(len(path) >= len(prefix) and _matches(path[:len(prefix)], prefix)
               for prefix in _EXEMPT_PREFIXES)


def _display(path: tuple) -> str:
    return ".".join(str(part) for part in path) or "<root>"


def _check_allowed_string(value: str, path: tuple, ports: tuple[str, ...]) -> None:
    for match in _BRACE_RE.finditer(value):
        token = match.group(1)
        if token in {"slug", "SLUG", "ref_branch"}:
            continue
        if token.startswith("port:"):
            name = token[len("port:"):]
            if name in ports:
                continue
            raise StackTemplateError(
                TEMPLATE_INVALID,
                f"{_display(path)} uses {{port:{name}}} but stack.ports does not declare {name!r}")
        raise StackTemplateError(
            TEMPLATE_INVALID, f"{_display(path)} contains an unknown placeholder {{{token}}}")
    if "{" in _BRACE_RE.sub("", value) or "}" in _BRACE_RE.sub("", value):
        raise StackTemplateError(TEMPLATE_INVALID, f"{_display(path)} contains an unbalanced brace")


def _scan(node: object, path: tuple, ports: tuple[str, ...]) -> None:
    if _is_exempt(path):
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and _BRACE_RE.search(key):
                raise StackTemplateError(
                    TEMPLATE_INVALID, f"{_display(path + (key,))}: mapping keys cannot hold placeholders")
            _scan(value, path + (key,), ports)
        return
    if isinstance(node, list):
        for index, item in enumerate(node):
            _scan(item, path + (index,), ports)
        return
    if not isinstance(node, str):
        return
    if _is_allowed(path):
        _check_allowed_string(node, path, ports)
    elif _BRACE_RE.search(node):
        raise StackTemplateError(
            TEMPLATE_INVALID,
            f"{_display(path)} contains a brace pattern; placeholders are allowed only in "
            "project, host.primary, alias hostnames, deploy.allowed_branches, "
            "secrets.values values and secrets.generated source names")


def parse_template(manifest: dict) -> StackTemplate:
    """Validate a template's ``stack:`` block and placeholder placement."""
    if not isinstance(manifest, dict):
        raise StackTemplateError(TEMPLATE_INVALID, "sandbox.hosting.yml must contain a mapping")
    block = manifest.get(STACK_KEY)
    if not isinstance(block, dict):
        raise StackTemplateError(TEMPLATE_INVALID, "a stack template needs a stack: mapping")
    if INSTANCE_KEY in manifest:
        raise StackTemplateError(TEMPLATE_INVALID, "a template cannot carry stack_instance")
    unknown = set(block) - {"slug", "ports"}
    if unknown:
        raise StackTemplateError(
            TEMPLATE_INVALID, "unknown stack keys: " + ", ".join(sorted(map(str, unknown))))
    pattern = block.get("slug")
    if not isinstance(pattern, str) or not pattern:
        raise StackTemplateError(TEMPLATE_INVALID, "stack.slug must be a regular expression string")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise StackTemplateError(TEMPLATE_INVALID, f"stack.slug is not a valid pattern: {exc}") from None
    ports = block.get("ports", [])
    if not isinstance(ports, list) or not all(isinstance(name, str) for name in ports):
        raise StackTemplateError(TEMPLATE_INVALID, "stack.ports must be a list of port names")
    if len(set(ports)) != len(ports):
        raise StackTemplateError(TEMPLATE_INVALID, "stack.ports names must be unique")
    for name in ports:
        if not _PORT_NAME_RE.fullmatch(name):
            raise StackTemplateError(
                TEMPLATE_INVALID, f"stack.ports name {name!r} must use lowercase letters, digits and hyphens")
    raw = {key: value for key, value in manifest.items()
           if key != STACK_KEY and not str(key).startswith("_")}
    _scan(raw, (), tuple(ports))
    project = raw.get("project")
    if isinstance(project, str) and "{port:" in project:
        raise StackTemplateError(TEMPLATE_INVALID, "project cannot use {port:...}; ports belong to the project")
    return StackTemplate(slug_pattern=compiled, ports=tuple(ports), raw=raw)


def validate_slug(template: StackTemplate, slug: object) -> str:
    if not isinstance(slug, str) or not _SAFE_SLUG_RE.fullmatch(slug):
        raise StackTemplateError(
            SLUG_INVALID, "stack name must use lowercase letters, digits and hyphens (max 63)")
    if not template.slug_pattern.fullmatch(slug):
        raise StackTemplateError(
            SLUG_INVALID, f"stack name {slug!r} does not match stack.slug {template.slug_pattern.pattern!r}")
    return slug


def upper_slug(slug: str) -> str:
    return slug.upper().replace("-", "_")


def _substitute(value: str, context: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        token = match.group(1)
        if token not in context:
            raise StackTemplateError(TEMPLATE_INVALID, f"no value for placeholder {{{token}}}")
        return context[token]
    return _BRACE_RE.sub(replace, value)


def _render_node(node: object, path: tuple, context: dict[str, str]) -> object:
    if isinstance(node, dict):
        return {key: _render_node(value, path + (key,), context) for key, value in node.items()}
    if isinstance(node, list):
        return [_render_node(item, path + (index,), context) for index, item in enumerate(node)]
    if isinstance(node, str) and _is_allowed(path) and not _is_exempt(path):
        return _substitute(node, context)
    return node


def render_project(template: StackTemplate, slug: str, ref: StackRef) -> str:
    """The rendered project name, needed before ports are allocated."""
    project = template.raw.get("project")
    if not isinstance(project, str):
        raise StackTemplateError(TEMPLATE_INVALID, "project must be a string")
    return _substitute(project, _context(slug, ref, {}))


def _context(slug: str, ref: StackRef, ports: dict[str, int]) -> dict[str, str]:
    context = {"slug": slug, "SLUG": upper_slug(slug), "ref_branch": ref.ref_branch}
    context.update({f"port:{name}": str(int(port)) for name, port in ports.items()})
    return context


def template_source_root(template: StackTemplate, template_relpath: str) -> PurePosixPath:
    """The template's source root relative to its checkout root."""
    declared = template.raw.get("source_root", template.raw.get("project_root", "."))
    if not isinstance(declared, str) or not declared.strip() or Path(declared).is_absolute():
        raise StackTemplateError(TEMPLATE_INVALID, "source_root must be a non-empty relative directory")
    parts: list[str] = []
    for part in (*PurePosixPath(template_relpath).parent.parts, *PurePosixPath(declared).parts):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise StackTemplateError(TEMPLATE_INVALID, "source_root must stay within the template checkout")
            parts.pop()
            continue
        parts.append(part)
    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def render(template: StackTemplate, *, slug: str, ref: StackRef, ports: dict[str, int],
           source_root: str | Path, template_path: str | Path) -> dict:
    """Render one stack manifest. ``source_root`` must be absolute."""
    validate_slug(template, slug)
    missing = set(template.ports) - set(ports)
    if missing:
        raise StackTemplateError(TEMPLATE_INVALID, "no port allocated for: " + ", ".join(sorted(missing)))
    if not Path(source_root).is_absolute():
        raise ValueError("rendered source_root must be absolute")
    rendered = _render_node(copy.deepcopy(template.raw), (), _context(slug, ref, ports))
    environments = rendered.get("environments")
    if isinstance(environments, dict):
        for name, env in environments.items():
            deploy = env.get("deploy") if isinstance(env, dict) else None
            branches = deploy.get("allowed_branches") if isinstance(deploy, dict) else None
            if isinstance(branches, list) and any(
                    isinstance(item, str) and not item.strip() for item in branches):
                raise StackTemplateError(
                    BRANCH_UNAVAILABLE,
                    f"environments.{name}.deploy.allowed_branches renders an empty branch; "
                    f"--ref {ref.ref!r} is a detached commit, so {{ref_branch}} has no value. "
                    "Pass a branch to --ref, or allow the commit with a literal or glob entry")
    rendered.pop("project_root", None)
    rendered["source_root"] = str(source_root)
    rendered[INSTANCE_KEY] = {
        "slug": slug,
        "ref": ref.ref,
        "ref_branch": ref.ref_branch,
        "commit": ref.commit,
        "template": str(template_path),
        "ports": {name: int(port) for name, port in sorted(ports.items())},
    }
    return rendered


def rendered_instance(manifest: dict, manifest_path: str | Path, stacks_root: str | Path) -> dict:
    """Validate a rendered manifest's provenance and return its stack facts.

    The absolute ``source_root`` a rendered manifest carries is accepted only
    for the exact Sandbox-owned location ``<stacks_root>/<slug>/sandbox.hosting.yml``.
    """
    block = manifest.get(INSTANCE_KEY)
    if not isinstance(block, dict):
        raise StackTemplateError(TEMPLATE_INVALID, "stack_instance must be a mapping")
    slug = block.get("slug")
    if not isinstance(slug, str) or not _SAFE_SLUG_RE.fullmatch(slug):
        raise StackTemplateError(TEMPLATE_INVALID, "stack_instance.slug is invalid")
    expected = (Path(stacks_root) / slug / MANIFEST_NAME).resolve()
    if Path(manifest_path).resolve() != expected:
        raise StackTemplateError(
            TEMPLATE_INVALID,
            "a rendered stack manifest is valid only at its Sandbox runtime path; "
            "use `sb host stack up` instead of copying it")
    commit = block.get("commit")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise StackTemplateError(TEMPLATE_INVALID, "stack_instance.commit must be a full commit")
    ref_branch = block.get("ref_branch", "")
    if not isinstance(ref_branch, str):
        raise StackTemplateError(TEMPLATE_INVALID, "stack_instance.ref_branch must be a string")
    ports = block.get("ports") or {}
    if not isinstance(ports, dict) or not all(
            isinstance(name, str) and isinstance(port, int) and not isinstance(port, bool)
            for name, port in ports.items()):
        raise StackTemplateError(TEMPLATE_INVALID, "stack_instance.ports must map names to ports")
    source_root = manifest.get("source_root")
    if not isinstance(source_root, str) or not Path(source_root).is_absolute():
        raise StackTemplateError(TEMPLATE_INVALID, "a rendered stack manifest needs an absolute source_root")
    return {"slug": slug, "ref": str(block.get("ref") or ""), "ref_branch": ref_branch,
            "commit": commit, "template": str(block.get("template") or ""),
            "ports": dict(ports), "source_root": source_root}


def branch_allowed(branch: str | None, patterns: list) -> bool:
    """Match a branch against exact names, ``"*"`` and fnmatch globs.

    A detached source (no branch) is allowed only by a literal ``"*"``.
    """
    if "*" in patterns:
        return True
    if not branch:
        return False
    return any(isinstance(pattern, str) and fnmatch.fnmatchcase(branch, pattern)
               for pattern in patterns)
