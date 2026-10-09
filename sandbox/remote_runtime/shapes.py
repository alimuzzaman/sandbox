"""Control-protocol shape guard (spec 061 FR-006).

The controller-to-runtime transports and the programs they run on the remote
exchange JSON payloads and receipts. Their shape is fingerprinted as the set
of string keys each source file builds or reads (dict literal keys, constant
subscripts, ``.get``/``.pop``/``.setdefault`` keys) and recorded, with the
protocol version it belongs to, in ``control_shapes.json``.

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
    "sandbox/remote_runtime/pins.py",
)

_KEY_METHODS = frozenset({"get", "pop", "setdefault"})


def _text(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def payload_keys(source: str) -> list[str]:
    """Sorted string keys a module builds or reads."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            keys.update(key for key in map(_text, node.keys) if key is not None)
        elif isinstance(node, ast.Subscript):
            key = _text(node.slice)
            if key is not None:
                keys.add(key)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _KEY_METHODS and node.args):
            key = _text(node.args[0])
            if key is not None:
                keys.add(key)
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
        if (recorded.get("spoken") == spoken
                and shape_diff(recorded.get("shapes", {}), shapes)):
            raise ValueError(
                f"control payload keys changed under protocol {spoken}; bump "
                "CONTROL_PROTOCOL_SPOKEN in sandbox/remote_runtime/protocol.py first")
        if isinstance(recorded.get("spoken"), int) and spoken < recorded["spoken"]:
            raise ValueError("protocol version is older than the recorded one")
    manifest = {"spoken": spoken, "shapes": shapes}
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
    return 0 if not diff and recorded.get("spoken") == CONTROL_PROTOCOL_SPOKEN else 1


if __name__ == "__main__":
    raise SystemExit(main())
