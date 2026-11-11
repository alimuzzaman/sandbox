"""Fixed intended provider consumers, never arbitrary HTTP/command dispatch.

One DNS mutation per broker effect. The registered lifecycle resolver must bind
the exact action/hostname to its measured target before supervisor composition.
These nonsecret action records alone confer no authority or installed support.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import re

from .models import CredentialIntent


class ConsumerError(ValueError):
    def __init__(self, code="credential_consumer_denied"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CloudflareRecordAction:
    operation: str
    zone_id: str
    hostname: str
    address: str
    proxied: bool
    record_id: str | None = None

    def __post_init__(self):
        if (self.operation not in ("create_address", "update_address")
                or not isinstance(self.zone_id, str)
                or re.fullmatch(r"[0-9a-f]{32}", self.zone_id) is None
                or type(self.proxied) is not bool
                or not isinstance(self.hostname, str) or len(self.hostname) > 253
                or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                                r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
                                self.hostname) is None):
            raise ConsumerError()
        if self.operation == "create_address":
            if self.record_id is not None:
                raise ConsumerError()
        elif not isinstance(self.record_id, str) or re.fullmatch(r"[0-9a-f]{32}", self.record_id) is None:
            raise ConsumerError()
        try:
            ipaddress.ip_address(self.address)
        except (ValueError, TypeError):
            raise ConsumerError() from None


@dataclass(frozen=True, slots=True)
class CloudflareRecordConsumer:
    """An explicitly broker-authenticated token goes to one fixed API request.

    Destination, version and purpose are fixed; lifecycle planning supplies only
    validated nonsecret DNS data. No local token or configuration fallback occurs.
    Arbitrary provider responses/exceptions never leave this consumer.
    """
    intent_digest: str
    action: CloudflareRecordAction = field(repr=False)
    consumer_id: str = field(default="cloudflare-dns-address", init=False)
    consumer_revision: str = field(default="cloudflare-dns-address-v1", init=False)
    purpose: str = field(default="dns-address", init=False)

    def __post_init__(self):
        if (not isinstance(self.intent_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", self.intent_digest) is None
                or type(self.action) is not CloudflareRecordAction):
            raise ConsumerError()

    def matches(self, intent: CredentialIntent) -> bool:
        selected = tuple(item for item in intent.mappings if item.consumer_id == self.consumer_id)
        return (self.intent_digest == intent.intent_digest() and len(selected) == 1
                and selected[0].kind == "provider"
                and selected[0].consumer_revision == self.consumer_revision
                and selected[0].purpose == self.purpose
                and selected[0].destination == "CLOUDFLARE_API_TOKEN")

    def consume(self, slots):
        if (type(slots) is not dict or set(slots) != {"CLOUDFLARE_API_TOKEN"}
                or not isinstance(slots["CLOUDFLARE_API_TOKEN"], str)
                or not slots["CLOUDFLARE_API_TOKEN"].strip()):
            return "failed"
        # Validate nonempty explicit credentials before Client construction, so
        # its legacy falsey-input local lookup is unreachable on this path.
        token = slots["CLOUDFLARE_API_TOKEN"].strip()
        outcome = "effects_unknown"
        client = None
        try:
            from sandbox.core._cloudflare import Client, DNS_ONLY_TTL_SECONDS
            action = self.action
            address = ipaddress.ip_address(action.address)
            body = {"type": "AAAA" if address.version == 6 else "A",
                    "name": action.hostname, "content": str(address),
                    "proxied": action.proxied,
                    "ttl": 1 if action.proxied else DNS_ONLY_TTL_SECONDS}
            path = "/zones/" + action.zone_id + "/dns_records"
            method = "POST"
            if action.operation == "update_address":
                path += "/" + action.record_id
                method = "PATCH"
            client = Client(token=token)
            # No list/upsert helper: it could perform multiple requests with
            # no intervening credential/held-writer check. This effect is one
            # fixed API request; each subsequent effect needs a new checkpoint.
            client._request(method, path, body)
            outcome = "succeeded"
        except Exception:
            # Even an HTTP failure can leave uncertain provider effects. Do not
            # return provider bodies, error text, headers or exception chains.
            outcome = "effects_unknown"
        finally:
            if client is not None:
                client.token = ""
            token = None
        return outcome


__all__ = ["ConsumerError", "CloudflareRecordAction", "CloudflareRecordConsumer"]
