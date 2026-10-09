"""Control-protocol shape guard (spec 061 FR-006).

The controller-to-runtime transports, the programs they run on the remote and
the installed runtime's response producers exchange JSON payloads and receipts. Each source file's shape is fingerprinted
as the string keys it builds or reads (dict literal keys, constant subscripts,
``.get``/``.pop``/``.setdefault`` keys) plus one signature per dict literal
naming the payload it builds (enclosing function, then the name, keyword or
key it is bound to) with its key set, counted, e.g.
``f:payload={a,b}`` or ``f:payload={a,b}*2``. Moving a key between payloads,
or dropping one from one of several identical payloads, therefore changes the
fingerprint. A Python program embedded as a string constant (one sent over SSH
and run on the remote, single-line or not) is parsed and fingerprinted the
same way, scoped under the constant's owner. Receipts whose keys come from
elsewhere (a registry schema, a helper) are also sampled from the real
producers (``receipts.py``) and recorded as ``receipt:<name>`` key paths. The
result is recorded, with the protocol version it belongs to, in
``control_shapes.json``.

``sandbox/remote_runtime/pins.py`` is deliberately not a source. Its program is
sent by the controller and run by the remote ``python3`` directly; the
installed runtime never executes or reads it, so its keys are not part of the
controller-to-runtime protocol (FR-006). Pin records are versioned by
``pins.SCHEMA`` instead, and ``tests/test_remote_runtime_pins.py`` fails when
the record shape changes without a schema change.

A change to those keys without a ``CONTROL_PROTOCOL_SPOKEN`` bump fails
``tests/test_remote_runtime_protocol_shapes.py``. After bumping, record the new
shapes with ``python -m sandbox.remote_runtime.shapes --write``; the writer
refuses to record changed keys under the version already recorded.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path

from sandbox.remote_runtime.protocol import CONTROL_PROTOCOL_SPOKEN

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().with_name("control_shapes.json")

# The installed runtime's producers of the responses the transports read: the
# ``sb workspace`` and ``sb job-*`` handlers and the services and records they
# serialize (Sol R7-1). A key change here changes what older controllers read.
RUNTIME_PRODUCERS = (
    "sandbox/commands/workspaces.py",
    "sandbox/application/workspace_service.py",
    "sandbox/commands/jobs_runtime.py",
    "sandbox/application/job_service.py",
    "sandbox/jobs/models.py",
    "sandbox/jobs/listing.py",
    "sandbox/jobs/output.py",
    "sandbox/jobs/registry.py",
    "sandbox/workspaces/checkout.py",
)

# Controller-side transports plus the programs they execute on the remote,
# then the runtime-side response producers.
SHAPE_SOURCES = (
    "sandbox/transports/remote_hosting_activation.py",
    "sandbox/transports/remote_hosting_images.py",
    "sandbox/transports/remote_jobs.py",
    "sandbox/transports/remote_owned_storage.py",
    "sandbox/transports/remote_postgres_recovery.py",
    "sandbox/transports/remote_recovery.py",
    "sandbox/transports/remote_server_capture.py",
    "sandbox/transports/remote_sync.py",
    "sandbox/transports/remote_workspaces.py",
    "sandbox/recovery/server_capture_helper.py",
    "sandbox/recovery/postgres_helper.py",
) + RUNTIME_PRODUCERS

# Bumped when the fingerprint method changes (not the payloads): a manifest
# recorded with an older format may be re-recorded under the same protocol.
FORMAT = 8
_EMBEDDED_DEPTH = 2

_KEY_METHODS = frozenset({"get", "pop", "setdefault"})


def _text(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _embedded_program(text: str):
    """The parsed program if a string constant is Python source, else None."""
    if not text.strip() or not any(ch in text for ch in "\n;(="):
        return None
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    if all(isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.Name))
           for node in tree.body):
        return None  # prose, a docstring or a bare word, not a program
    return tree


def _parents(tree) -> dict:
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _target(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - an unprintable target still has a kind
        return type(node).__name__


def _site(node, parents):
    """``(scope, label, site node, key path)`` naming where a payload is bound.

    The key path holds every enclosing dict key up to the binding, so a
    nested ``source`` in a request and one in a response stay apart. The
    binding is an assignment target, ``callee(arg=)``, ``callee(index)``,
    ``return`` or, failing those, the enclosing statement (``_``).
    """
    label = site = None
    path, scope, current = [], [], node
    while current in parents:
        parent = parents[current]
        if label is None:
            if isinstance(parent, ast.Dict) and current in parent.values:
                key = _text(parent.keys[parent.values.index(current)])
                path.append(key if key is not None else "?")
            elif isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                label, site = ",".join(_target(t) for t in targets), parent
            elif isinstance(parent, ast.keyword):
                call = parents.get(parent)
                callee = _target(call.func) if isinstance(call, ast.Call) else ""
                label, site = f"{callee}({parent.arg}=)", parent
            elif isinstance(parent, ast.Call) and current in parent.args:
                label = f"{_target(parent.func)}({parent.args.index(current)})"
                site = parent
            elif isinstance(parent, ast.Return):
                label, site = "return", parent
            elif isinstance(parent, ast.stmt):
                label, site = "_", parent
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.append(parent.name)
        current = parent
    return ".".join(reversed(scope)), label or "_", site, "".join(f"[{k}]" for k in reversed(path))


def _namer(tree, parents):
    """Owner names for one tree; repeated sites in a scope are numbered.

    Numbering follows source order, so two calls with the same keyword or
    two return branches never share a name (Sol R3-1).
    """
    sites: dict[tuple[str, str], list] = {}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) or (isinstance(node, ast.Constant)
                                          and isinstance(node.value, str)):
            scope, label, site, path = _site(node, parents)
            found.append((node, scope, label, site, path))
            if site is not None:
                bucket = sites.setdefault((scope, label), [])
                if site not in bucket:
                    bucket.append(site)
    for bucket in sites.values():
        bucket.sort(key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
    names = {}
    for node, scope, label, site, path in found:
        ordinal = sites[(scope, label)].index(site) + 1 if site is not None else 1
        names[node] = f"{scope}:{label}" + (f"#{ordinal}" if ordinal > 1 else "") + path
    return names


def _store(target, key: str, parents) -> str:
    """``scope:payload[key]=`` for a field written into a payload after it is built."""
    return _scope(target, parents) + f":{_target(target)}[{key}]="


def _scope(node, parents) -> str:
    scope, current = [], node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.append(current.name)
    return ".".join(reversed(scope))


def _read_link(node):
    """``(inner, key)`` when ``node`` reads one string key from ``inner``."""
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        key = _text(node.slice)
        return (node.value, key) if key is not None else None
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("get", "pop") and node.args):
        key = _text(node.args[0])
        return (node.func.value, key) if key is not None else None
    return None


def _read_path(node, parents, prefix: str) -> str | None:
    """``scope:read[k1][k2]`` for a maximal read chain, else None.

    The root variable is left out, so a local rename keeps the fingerprint
    while moving a read between nested and root response paths changes it.
    """
    if _read_link(node) is None:
        return None
    parent = parents.get(node)
    if isinstance(parent, ast.Subscript) and parent.value is node and _read_link(parent):
        return None
    if isinstance(parent, ast.Attribute) and parent.value is node:
        call = parents.get(parent)
        if isinstance(call, ast.Call) and call.func is parent and _read_link(call):
            return None
    path, current = [], node
    while (link := _read_link(current)) is not None:
        current, key = link
        path.append(key)
    return prefix + _scope(node, parents) + ":read" + "".join(f"[{k}]" for k in reversed(path))


def _collect(tree, keys: set[str], signatures: list[str], depth: int, prefix: str = "",
             reads: set[str] | None = None) -> None:
    reads = set() if reads is None else reads
    parents = _parents(tree)
    names = _namer(tree, parents)
    for node in ast.walk(tree):
        read = _read_path(node, parents, prefix)
        if read is not None:
            reads.add(read)
        if isinstance(node, ast.Dict):
            literal = [key for key in map(_text, node.keys) if key is not None]
            keys.update(literal)
            if literal:
                signatures.append(prefix + names[node]
                                  + "={" + ",".join(sorted(set(literal))) + "}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and depth:
            embedded = _embedded_program(node.value)
            if embedded is not None:
                _collect(embedded, keys, signatures, depth - 1,
                         prefix + "<" + names[node] + ">", reads)
        elif isinstance(node, ast.Subscript):
            key = _text(node.slice)
            if key is not None:
                keys.add(key)
                if isinstance(node.ctx, ast.Store):
                    signatures.append(prefix + _store(node.value, key, parents))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _KEY_METHODS and node.args):
            key = _text(node.args[0])
            if key is not None:
                keys.add(key)
                if node.func.attr == "setdefault":
                    signatures.append(prefix + _store(node.func.value, key, parents))


def payload_keys(source: str) -> list[str]:
    """Sorted keys and counted payload signatures of a module and its embedded programs."""
    keys: set[str] = set()
    signatures: list[str] = []
    reads: set[str] = set()
    _collect(ast.parse(source), keys, signatures, _EMBEDDED_DEPTH, "", reads)
    counted = Counter(signatures)
    return sorted(keys | reads | {sig if n == 1 else f"{sig}*{n}" for sig, n in counted.items()})


def current_shapes(root: Path = ROOT, sources=SHAPE_SOURCES) -> dict[str, list[str]]:
    """Source fingerprints plus, for the default sources, sampled runtime receipts."""
    shapes = {path: payload_keys((root / path).read_text(encoding="utf-8")) for path in sources}
    if sources == SHAPE_SOURCES:
        from sandbox.remote_runtime.receipts import receipt_shapes
        shapes.update(receipt_shapes())
    return shapes


def read_manifest(path: Path = MANIFEST) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def shape_diff(recorded: dict, current: dict) -> dict[str, dict[str, list[str]]]:
    """Per source, the keys added and removed since the recorded shapes."""
    diff = {}
    for path in sorted(set(recorded) | set(current)):
        before, after = set(recorded.get(path, ())), set(current.get(path, ()))
        if before != after:
            diff[path] = {"added": sorted(after - before), "removed": sorted(before - after)}
    return diff


def write_manifest(path: Path = MANIFEST, *, spoken: int = CONTROL_PROTOCOL_SPOKEN,
                   shapes: dict | None = None) -> dict:
    """Record shapes for ``spoken``; refuse changed keys under a recorded version."""
    shapes = current_shapes() if shapes is None else shapes
    if path.exists():
        recorded = read_manifest(path)
        if (recorded.get("spoken") == spoken and recorded.get("format", 1) == FORMAT
                and shape_diff(recorded.get("shapes", {}), shapes)):
            raise ValueError(
                f"control payload keys changed under protocol {spoken}; bump "
                "CONTROL_PROTOCOL_SPOKEN in sandbox/remote_runtime/protocol.py first")
        if isinstance(recorded.get("spoken"), int) and spoken < recorded["spoken"]:
            raise ValueError("protocol version is older than the recorded one")
    manifest = {"format": FORMAT, "spoken": spoken, "shapes": shapes}
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sandbox.remote_runtime.shapes")
    parser.add_argument("--write", action="store_true",
                        help="record the current shapes for the current protocol version")
    args = parser.parse_args(argv)
    if args.write:
        try:
            write_manifest()
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"recorded control shapes for protocol {CONTROL_PROTOCOL_SPOKEN}")
        return 0
    recorded = read_manifest()
    diff = shape_diff(recorded.get("shapes", {}), current_shapes())
    print(json.dumps({"recorded_spoken": recorded.get("spoken"),
                      "spoken": CONTROL_PROTOCOL_SPOKEN, "diff": diff}, indent=1))
    return 0 if (not diff and recorded.get("spoken") == CONTROL_PROTOCOL_SPOKEN
                 and recorded.get("format", 1) == FORMAT) else 1


if __name__ == "__main__":
    raise SystemExit(main())
