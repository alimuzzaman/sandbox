"""Operation-held generated values delivered only to fixed runtime recipients.

No values enter a ledger, file or response. The Docker intended-recipient
facility retains its own application configuration under the reviewed custody
policy. A crash cannot reconstruct this bag or reexecute a reserved effect.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import secrets
import threading

from .admission import require_committed
from .service import require_current_consumer_effect

_BAGS = {}
_LOCK = threading.Lock()


class RuntimeCredentialError(ValueError):
    def __init__(self, code="credential_generated_authority_unavailable"):
        self.code = code
        super().__init__(code)


@contextmanager
def _hold_runtime_credentials(receipt, *, intent):
    """Trusted dispatcher scope, no generation until a genuine effect starts."""
    require_committed(receipt, intent_digest=intent.intent_digest())
    key = (os.getpid(), threading.get_ident(), intent.intent_digest())
    entry = (receipt, {})
    with _LOCK:
        if key in _BAGS:
            raise RuntimeCredentialError()
        _BAGS[key] = entry
    try:
        yield
        require_committed(receipt, intent_digest=intent.intent_digest())
    finally:
        with _LOCK:
            if _BAGS.get(key) is entry:
                del _BAGS[key]
        entry[1].clear()


def _generated_slots(receipt, *, intent, consumer_id):
    """Broker-internal value handoff, never exposed through transport."""
    from .application import RuntimeEnvironmentConsumer
    from .manifest import require_consumer
    consumer = require_consumer(intent, consumer_id=consumer_id)
    if type(consumer) is not RuntimeEnvironmentConsumer:
        raise RuntimeCredentialError()
    checkpoint = require_current_consumer_effect(intent_digest=intent.intent_digest(),
                                                consumer_id=consumer_id)
    require_committed(receipt, intent_digest=intent.intent_digest())
    key = (os.getpid(), threading.get_ident(), intent.intent_digest())
    with _LOCK:
        entry = _BAGS.get(key)
    if entry is None or entry[0] is not receipt:
        raise RuntimeCredentialError()
    selected = tuple(item for item in intent.mappings
                     if item.consumer_id == consumer_id and item.kind == "generated")
    if selected and not intent.generate_requested:
        raise RuntimeCredentialError("credential_runtime_reuse_unavailable")
    # This is distinct generated authority. Provider mappings can never look
    # up a bag entry or be satisfied by a fresh random value.
    slots = {}
    for mapping in selected:
        checkpoint.require_current()
        if mapping.source_key not in entry[1]:
            entry[1][mapping.source_key] = secrets.token_urlsafe(32)
        slots[mapping.destination] = entry[1][mapping.source_key]
    checkpoint.require_current()
    return slots
