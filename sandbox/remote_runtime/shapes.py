"""Control-protocol shape guard (spec 061 FR-006).

The controller-to-runtime transports and the programs they run on the remote
exchange JSON payloads and receipts. Each source file's shape is fingerprinted
as the string keys it builds or reads (dict literal keys, constant subscripts,
``.get``/``.pop``/``.setdefault`` keys) plus one signature per dict literal
naming the payload it builds (enclosing function, then the name, keyword or
key it is bound to) with its key set, counted, e.g.
``f:payload={a,b}`` or ``f:payload={a,b}*2``. Moving a key between payloads,
or dropping one from one of several identical payloads, therefore changes the
fingerprint. A Python program embedded as a string constant (one sent over SSH
and run on the remote, single-line or not) is parsed and fingerprinted the
same way, scoped under the constant's owner. The result is recorded, with the
protocol version it belongs to, in ``control_shapes.json``.

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

# Controller-side transports plus the programs they execute on the remote.
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
)

# Bumped when the fingerprint method changes (not the payloads): a manifest
# recorded with an older format may be re-recorded under the same protocol.
FORMAT = 3
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


def _owner(node, parents) -> str:
    """``scope:binding`` for a dict literal or string constant."""
    binding, scope, current = None, [], node
    while current in parents:
        parent = parents[current]
        if binding is None:
            if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                binding = ",".join(_target(t) for t in targets)
            elif isinstance(parent, ast.keyword):
                binding = f"{parent.arg}="
            elif isinstance(parent, ast.Return):
                binding = "return"
            elif isinstance(parent, ast.Dict) and current in parent.values:
                key = _text(parent.keys[parent.values.index(current)])
                binding = f"[{key}]" if key is not None else None
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.append(parent.name)
        current = parent
    return ".".join(reversed(scope)) + ":" + (binding or "_")


def _collect(tree, keys: set[str], signatures: list[str], depth: int, prefix: str = "") -> None:
    parents = _parents(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            literal = [key for key in map(_text, node.keys) if key is not None]
            keys.update(literal)
            if literal:
                signatures.append(prefix + _owner(node, parents)
                                  + "={" + ",".join(sorted(set(literal))) + "}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and depth:
            embedded = _embedded_program(node.value)
            if embedded is not None:
                _collect(embedded, keys, signatures, depth - 1,
                         prefix + "<" + _owner(node, parents) + ">")
        elif isinstance(node, ast.Subscript):
            key = _text(node.slice)
            if key is not None:
                keys.add(key)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _KEY_METHODS and node.args):
            key = _text(node.args[0])
            if key is not None:
                keys.add(key)


def payload_keys(source: str) -> list[str]:
    """Sorted keys and counted payload signatures of a module and its embedded programs."""
    keys: set[str] = set()
    signatures: list[str] = []
    _collect(ast.parse(source), keys, signatures, _EMBEDDED_DEPTH)
    counted = Counter(signatures)
    return sorted(keys | {sig if n == 1 else f"{sig}*{n}" for sig, n in counted.items()})


def current_shapes(root: Path = ROOT, sources=SHAPE_SOURCES) -> dict[str, list[str]]:
    return {path: payload_keys((root / path).read_text(encoding="utf-8")) for path in sources}


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
