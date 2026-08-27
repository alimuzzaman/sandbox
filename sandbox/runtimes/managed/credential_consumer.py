"""Permanent refusal facade for the historical managed v1 consumer."""


class CredentialConsumerError(ValueError):
    """Fixed closed error retained for import compatibility."""


class ExplicitCredentialConsumer:
    """Historical name retained without any broker or ``handle`` capability."""

    def __init__(self, *_args, **_kwargs) -> None:
        raise CredentialConsumerError("credential_consumer_v1_disabled")


__all__ = ["CredentialConsumerError", "ExplicitCredentialConsumer"]
