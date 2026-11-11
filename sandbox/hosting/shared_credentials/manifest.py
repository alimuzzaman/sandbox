"""Fixed process-local consumer manifest, not an installed handler factory.

Only reviewed registered lifecycle composition may enter this scope, after
resolving exact target/artifact/DNS intent. No HTTP caller supplies consumers or
callbacks. An absent scope refuses rather than inventing installed capability.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import threading
from types import MappingProxyType

from .admission import require_committed
from .consumers import CloudflareRecordConsumer, ConsumerError
from .models import CredentialIntent


_REGISTRY = {}
_LOCK = threading.Lock()


@contextmanager
def _bind_fixed_consumers(receipt, *, intent: CredentialIntent, consumers: tuple):
    """Private trusted lifecycle adapter, never a request/callback registry."""
    if type(intent) is not CredentialIntent or type(consumers) is not tuple or not consumers:
        raise ConsumerError()
    row = require_committed(receipt, intent_digest=intent.intent_digest())
    if row.canonical_intent != intent.canonical_json():
        raise ConsumerError()
    selected = {}
    for consumer in consumers:
        # Explicit source-owned allowlist; registration cannot wrap arbitrary
        # callbacks, subclasses, commands, URL handlers or user Python objects.
        if (type(consumer) is not CloudflareRecordConsumer or not consumer.matches(intent)
                or consumer.consumer_id in selected):
            raise ConsumerError()
        receipt._issuer._check_consumer(receipt, intent, consumer)
        selected[consumer.consumer_id] = consumer
    declared = {item.consumer_id for item in intent.mappings if item.kind == "provider"}
    if set(selected) != declared:
        raise ConsumerError()
    key = (os.getpid(), threading.get_ident(), intent.intent_digest())
    entry = (receipt, intent, MappingProxyType(selected))
    with _LOCK:
        if key in _REGISTRY:
            raise ConsumerError("credential_consumer_scope_busy")
        _REGISTRY[key] = entry
    try:
        yield
    finally:
        with _LOCK:
            if _REGISTRY.get(key) is entry:
                del _REGISTRY[key]


def require_consumer(intent: CredentialIntent, *, consumer_id: str):
    if type(intent) is not CredentialIntent:
        raise ConsumerError()
    key = (os.getpid(), threading.get_ident(), intent.intent_digest())
    with _LOCK:
        entry = _REGISTRY.get(key)
    if entry is None:
        raise ConsumerError()
    receipt, retained_intent, consumers = entry
    row = require_committed(receipt, intent_digest=intent.intent_digest())
    consumer = consumers.get(consumer_id)
    if (retained_intent.canonical_json() != intent.canonical_json()
            or row.canonical_intent != intent.canonical_json()
            or consumer is None or not consumer.matches(intent)):
        raise ConsumerError()
    receipt._issuer._check_consumer(receipt, intent, consumer)
    return consumer


__all__ = ["require_consumer"]
