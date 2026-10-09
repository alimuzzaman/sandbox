"""Control-protocol shape guard (spec 061 FR-006).

The controller-to-runtime transports and the programs they run on the remote
exchange JSON payloads and receipts. Each source file's shape is fingerprinted
as the string keys it builds or reads (dict literal keys, constant subscripts,
``.get``/``.pop``/``.setdefault`` keys) plus the key set of every dict literal
(``{a,b}``), so moving an existing key into another payload also counts. A
Python program embedded as a string constant (one sent over SSH and run on the
remote) is parsed and fingerprinted the same way. The result is recorded, with
the protocol version it belongs to, in ``control_shapes.json``.

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
FORMAT = 2
_EMBEDDED_DEPTH = 2

_KEY_METHODS = frozenset({"get", "pop", "setdefault"})


def _text(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _embedded_program(text: str):
    """The parsed program if a string constant is Python source, else None."""
    if "\n" not in text:
        return None
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    if all(isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
           for node in tree.body):
        return None  # prose or a docstring, not a program
    return tree


def _collect(tree, keys: set[str], depth: int) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            literal = [key for key in map(_text, node.keys) if key is not None]
            keys.update(literal)
            if literal:
                keys.add("{" + ",".join(sorted(set(literal))) + "}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and depth:
            embedded = _embedded_program(node.value)
            if embedded is not None:
                _collect(embedded, keys, depth - 1)
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
    """Sorted keys and dict-literal key sets a module (and its embedded programs) uses."""
    keys: set[str] = set()
    _collect(ast.parse(source), keys, _EMBEDDED_DEPTH)
    return sorted(keys)


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
