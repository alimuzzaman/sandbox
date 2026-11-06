"""Read and retain the shared-zone claims owned by one hosting target."""


def shared_claims(record: dict | None) -> list[dict]:
    """Return distinct current and pending claims, including legacy single markers."""
    record = record or {}
    pending = record.get("coverage_release_pending")
    pending = pending if isinstance(pending, list) else [pending]
    claims = []
    for claim in [record.get("dns_coverage"), *pending]:
        if (isinstance(claim, dict) and claim.get("mode") == "shared"
                and isinstance(claim.get("zone"), str) and claim["zone"]
                and not any(item["zone"] == claim["zone"] for item in claims)):
            claims.append(dict(claim))
    return claims


def set_pending(record: dict, claims: list[dict]) -> None:
    """Store one legacy-shaped marker or a list when several claims remain."""
    if not claims:
        record.pop("coverage_release_pending", None)
    elif len(claims) == 1:
        record["coverage_release_pending"] = {"mode": "shared", "zone": claims[0]["zone"]}
    else:
        record["coverage_release_pending"] = [{"mode": "shared", "zone": claim["zone"]} for claim in claims]
