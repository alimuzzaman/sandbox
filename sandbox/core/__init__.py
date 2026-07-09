"""Sandbox shared core — split into readable submodules; the namespace is
back-filled across them so cross-module references resolve at call time
(grouping is for readability, not import order). `from sandbox.core import *`
still works for the command modules."""
import types as _types
from importlib import import_module

_SUBMODS = ['asyncjobs', 'bridge', 'config', 'dash', 'docker', 'domains',
            'fanout', 'herd', 'instances', 'integ', 'licensing', 'misc',
            'paths', 'provision', 'schema_catalog', 'tests', 'ui']
_mods = [import_module(f'sandbox.core._{m}') for m in _SUBMODS]

# Collect every name defined across the submodules (functions, classes,
# constants) — but NOT the modules they imported (os, re, …) or dunders.
_ns = {}
for _m in _mods:
    for _k, _v in vars(_m).items():
        if _k.startswith('__'):
            continue
        if isinstance(_v, _types.ModuleType):
            continue
        _ns[_k] = _v

# expose at the package level …
globals().update(_ns)
# … and back-fill into every submodule so a function in one submodule can call a
# name defined in another (resolved at call time — no import-cycle risk).
for _m in _mods:
    for _k, _v in _ns.items():
        _m.__dict__.setdefault(_k, _v)

__all__ = [k for k in _ns if not k.startswith('__')]
