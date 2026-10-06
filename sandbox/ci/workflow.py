"""Side-effect-free remote CI workflow loading, graphing, and preflight."""

from __future__ import annotations
import re

from pathlib import Path
from typing import Any

from .compatibility import CATALOG_VERSION, detect


_UNKNOWN_MUTATION_MARKERS = ("mutat", "webhook", "issue-comment", "pull-request-comment")

# Deploy-class actions. Matched on the action name (before ``@``): an exact
# known prefix, or a name word that is itself a mutating verb.
_DEPLOY_ACTION_PREFIXES = frozenset({
    "10up/action-wordpress-plugin-deploy",
    "10up/action-wordpress-plugin-asset-update",
    "softprops/action-gh-release",
    "ncipollo/release-action",
    "actions/create-release",
    "actions/upload-release-asset",
    "actions/deploy-pages",
    "peaceiris/actions-gh-pages",
    "jamesives/github-pages-deploy-action",
    "svenstaro/upload-release-action",
    "pypa/gh-action-pypi-publish",
    "js-devtools/npm-publish",
    "ad-m/github-push-action",
    "docker/build-push-action",
})
_DEPLOY_ACTION_WORDS = frozenset({"deploy", "release", "publish", "push", "svn", "gh-pages"})

# A script, Make target, or executable whose whole name is a mutating verb.
_MUTATING_SCRIPT_NAMES = frozenset({"deploy", "publish", "release", "release:publish"})
# Wrappers and shell keywords that precede the real command.
_COMMAND_PREFIXES = frozenset({
    "sudo", "env", "time", "nohup", "exec", "command", "builtin", "!", "if", "then",
    "else", "elif", "do", "while", "until", "{", "(", "npx", "pnpx", "bunx",
})
_SHELL_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", ";;", "|&"})
_INTERPRETERS = frozenset({
    "bash", "sh", "zsh", "dash", "python", "python3", "node", "php", "ruby", "perl",
})
# (argv[0], subcommand words) pairs that publish, push, or release.
_MUTATING_SUBCOMMANDS = {
    "git": (("push",),),
    "svn": (("commit",), ("ci",)),
    "npm": (("publish",),),
    "pnpm": (("publish",),),
    "yarn": (("publish",), ("npm", "publish")),
    "bun": (("publish",),),
    "gh": (("release", "create"), ("release", "upload"), ("pr", "merge")),
    "twine": (("upload",),),
    "cargo": (("publish",),),
    "gem": (("push",),),
    "poetry": (("publish",),),
    "lerna": (("publish",),),
    "changeset": (("publish",),),
    "vsce": (("publish",),),
    "ovsx": (("publish",),),
    "docker": (("push",),),
    "dotnet": (("nuget", "push"),),
}
_MUTATING_EXECUTABLES = frozenset({"semantic-release"})
# Options that take a value, so the value is not mistaken for a subcommand.
_VALUE_OPTIONS = {"git": {"-C", "-c", "--git-dir", "--work-tree"},
                  "pnpm": {"-C", "--dir", "--filter", "-F"},
                  "npm": {"--prefix", "-w", "--workspace"},
                  "yarn": {"--cwd"}}


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    """Match a marker at a word start, ignoring command-line flags.

    Used only for the fail-closed unknown-mutation markers. Plain substring
    matching blocked `immutable`; flag tokens are dropped, and a marker must
    not follow a letter, digit or underscore.
    """
    words = " ".join(token for token in text.split() if not token.startswith("-"))
    return any(re.search(r"(?<![A-Za-z0-9_])" + re.escape(marker), words) for marker in markers)


def _shell_commands(script: str) -> list[list[str]]:
    """Split a run script into simple commands of shell words.

    Quoted strings stay single words, so text inside echo messages, URLs and
    assignments is never read as a command. A line shlex cannot parse (an
    unterminated multi-line quote) falls back to whitespace words.
    """
    import shlex

    commands: list[list[str]] = []
    joined = re.sub(r"\\\n", " ", script)
    for line in joined.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            tokens = list(lexer)
        except ValueError:
            tokens = line.split()
        current: list[str] = []
        for token in tokens:
            if token in _SHELL_SEPARATORS:
                if current:
                    commands.append(current)
                current = []
            else:
                current.append(token)
        if current:
            commands.append(current)
    return commands


def _strip_prefixes(argv: list[str]) -> list[str]:
    index = 0
    while index < len(argv):
        word = argv[index]
        if word in _COMMAND_PREFIXES or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
            index += 1
            continue
        if word.startswith("-") and index > 0 and argv[index - 1] in {"sudo", "env", "npx"}:
            index += 1
            continue
        if word in {"pnpm", "yarn"} and index + 1 < len(argv) and argv[index + 1] in {"exec", "dlx"}:
            index += 2
            continue
        break
    return argv[index:]


def _operands(program: str, words: list[str]) -> list[str]:
    """Positional words after the program, skipping options and their values."""
    takes_value = _VALUE_OPTIONS.get(program, set())
    result: list[str] = []
    skip = False
    for word in words:
        if skip:
            skip = False
            continue
        if word.startswith("-"):
            if word in takes_value:
                skip = True
            continue
        result.append(word)
    return result


_SCRIPT_SUFFIXES = (".sh", ".bash", ".py", ".js", ".mjs", ".cjs", ".ts", ".php", ".rb", ".pl")


def _script_stem(path: str) -> str:
    """File name without one script extension: deploy.sh -> deploy, but
    release.playwright.config.js -> release.playwright.config."""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    for suffix in _SCRIPT_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    return name


def _command_mutation(argv: list[str]) -> str | None:
    argv = _strip_prefixes(argv)
    if not argv:
        return None
    program = argv[0].rsplit("/", 1)[-1]
    operands = _operands(program, argv[1:])
    if program in _MUTATING_EXECUTABLES:
        return program
    for subcommand in _MUTATING_SUBCOMMANDS.get(program, ()):
        if tuple(operands[:len(subcommand)]) == subcommand:
            return " ".join((program, *subcommand))
    script = None
    if program in {"npm", "pnpm", "yarn", "bun", "composer"} and operands[:1] in (
            ["run"], ["run-script"]):
        script = operands[1] if len(operands) > 1 else None
    elif program in {"pnpm", "yarn"} and operands:
        script = operands[0]
    elif program in {"make", "just", "task", "rake"} and operands:
        if any(target in _MUTATING_SCRIPT_NAMES for target in operands):
            return f"{program} {next(t for t in operands if t in _MUTATING_SCRIPT_NAMES)}"
    elif program in _INTERPRETERS and operands:
        if _script_stem(operands[0]) in _MUTATING_SCRIPT_NAMES:
            return f"{program} {operands[0]}"
    elif argv[0].startswith(("./", "/", "../")) or "/" in argv[0]:
        if _script_stem(argv[0]) in _MUTATING_SCRIPT_NAMES:
            return argv[0]
    if script in _MUTATING_SCRIPT_NAMES:
        return f"{program} run {script}"
    return None


def _action_mutation(uses: str) -> str | None:
    name = uses.split("@", 1)[0].strip().lower()
    if name in _DEPLOY_ACTION_PREFIXES:
        return name
    words = set(re.split(r"[/_.]|-(?!pages)", name))
    if "gh-pages" in name or words & _DEPLOY_ACTION_WORDS:
        return name
    return None


def step_mutation(step: dict) -> str | None:
    """Name the publishing command or deploy-class action in a step, if any.

    Classification is by command, not substring: only a real push, publish,
    release upload, or a script whose name is exactly a mutating verb counts.
    Path segments, URLs, quoted text and names such as ``release:check`` do not.
    """
    uses = step.get("uses")
    if isinstance(uses, str) and uses.strip():
        return _action_mutation(uses)
    run = step.get("run")
    if not isinstance(run, str):
        return None
    for argv in _shell_commands(run):
        found = _command_mutation(argv)
        if found:
            return found
    return None


class WorkflowError(ValueError):
    pass


def load_workflow(project_root: str | Path, workflow_path: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = (root / workflow_path).resolve() if not Path(workflow_path).is_absolute() else Path(workflow_path).resolve()
    if root not in path.parents:
        raise WorkflowError("workflow path must remain under the project")
    try:
        import yaml
        value = yaml.safe_load(path.read_text()) or {}
    except OSError as exc:
        raise WorkflowError(f"could not read workflow: {exc}") from exc
    except Exception as exc:
        raise WorkflowError(f"workflow YAML is invalid: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("jobs"), dict):
        raise WorkflowError("workflow must contain a jobs mapping")
    return value


def matrix_cells(job: dict) -> list[dict]:
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if not isinstance(matrix, dict): return [{}]
    include = matrix.get("include")
    if include and not [key for key in matrix if key not in {"include", "exclude"}]:
        return [dict(value) for value in include]
    axes = {key: values for key, values in matrix.items() if key not in {"include", "exclude"}}
    cells = [{}]
    for key, values in axes.items():
        if not isinstance(values, list): raise WorkflowError(f"matrix axis {key!r} must be a list")
        cells = [{**cell, key: value} for cell in cells for value in values]
    return cells


def preflight(project_root: str | Path, workflow_path: str | Path, *, selected_jobs: list[str] | None = None,
              accepted_differences: list[str] | None = None, safe_mode: bool = True) -> dict:
    workflow = load_workflow(project_root, workflow_path)
    jobs = workflow["jobs"]
    selected = selected_jobs or list(jobs)
    unknown = [name for name in selected if name not in jobs]
    if unknown: raise WorkflowError(f"unknown workflow jobs: {', '.join(unknown)}")
    active_jobs = list(dict.fromkeys(selected))
    active_job_set = set(active_jobs)
    pending = list(selected)
    while pending:
        job_id = pending.pop()
        needs = jobs[job_id].get("needs", [])
        needs = needs if isinstance(needs, list) else [needs]
        for dependency in needs:
            if dependency not in jobs:
                raise WorkflowError(f"job {job_id!r} needs unknown job {dependency!r}")
            if dependency not in active_job_set:
                active_job_set.add(dependency)
                active_jobs.append(dependency)
                pending.append(dependency)

    differences = [item for item in detect(workflow) if not item["location"].startswith("jobs.") or
                   any(item["location"].startswith(f"jobs.{job_id}.") for job_id in active_job_set)]
    accepted = set(accepted_differences or ())
    for item in differences: item["accepted"] = item["id"] in accepted
    blocking = [item["id"] for item in differences if item["severity"] == "block" and not item["accepted"]]
    safe_actions = []
    for job_id in active_jobs:
        for index, step in enumerate(jobs[job_id].get("steps") or []):
            text = str(step.get("uses") or step.get("run") or "").lower()
            location = f"jobs.{job_id}.steps[{index}]"
            if _has_marker(text, _UNKNOWN_MUTATION_MARKERS):
                difference_id = f"safe-mode-unknown-mutation:{job_id}:{index}"
                safe_actions.append({"id": difference_id, "location": location, "action": "blocked"})
                differences.append({"id": difference_id, "workflow": str(workflow_path),
                    "location": location, "severity": "block", "accepted": False,
                    "detail": "unknown external mutation is blocked before execution",
                    "catalog_version": CATALOG_VERSION})
                blocking.append(difference_id)
            elif (mutation := step_mutation(step if isinstance(step, dict) else {})):
                difference_id = f"safe-mode:{job_id}:{index}"
                safe_actions.append({"id": difference_id, "location": location,
                                     "action": "neutralized" if safe_mode else "allowed",
                                     "command": mutation})
                if safe_mode:
                    differences.append({"id": difference_id, "workflow": str(workflow_path),
                        "location": location, "severity": "notice", "accepted": True,
                        "detail": ("deployment/release/publish step is neutralized in safe mode "
                                   f"({mutation})"),
                        "catalog_version": CATALOG_VERSION})
    return {"ok": not blocking, "compatible": not blocking, "catalog_version": CATALOG_VERSION,
            "engine": {"name": "act", "version": "observed-at-execution"},
            "runner": {"platform": "linux", "accepted": True},
            "graph": {"jobs": list(jobs), "selected_jobs": selected,
                      "dependencies": {name: jobs[name].get("needs", []) if isinstance(jobs[name].get("needs", []), list) else [jobs[name].get("needs")] for name in selected},
                      "matrix_cells": sum(len(matrix_cells(jobs[name])) for name in active_jobs)},
            "differences": differences, "safe_mode_actions": safe_actions, "blocking": sorted(set(blocking))}
