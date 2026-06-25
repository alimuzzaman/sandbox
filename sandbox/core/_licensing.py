"""Pro-license central store (spec 013).

ONE license key per family, stored ONCE for all instances in the gitignored
secret store (`sandbox.local.yml` under $SANDBOX_HOME) — the same secret store
that holds the per-instance bridge token. Keys are secrets: never committed,
never echoed (the `sb license` command masks them), never snapshotted.

Layout in sandbox.local.yml:

    licensing:
      wpdeveloper_key: "<secret>"          # one key for all WPDeveloper pro plugins
      elementor_pro_key: "<secret>"        # the Elementor Pro key
      elementor_primary_instance: "<name>" # non-secret: auto-recorded first activator
      elementor_primary_url: "https://…"   # non-secret: the pin target

The per-instance mu-plugin never reads this file directly; provisioning writes a
scoped `sandbox-licensing.json` into each instance (see _provision). This module
is the host-side read/write surface used by `sb license` and provisioning."""

# Families → the secret key name in the licensing block.
# NOTE: WPDeveloper is intentionally absent — its pro plugins are force-activated
# keylessly in-instance (the licensed download is 2FA/activation-gated, so a key
# buys nothing; see assets/licensing/platforms/wpdeveloper.php). Only vendors that
# genuinely need a managed secret (e.g. Elementor) live here.
LICENSE_FAMILIES = {
    "elementor": "elementor_pro_key",
}


def _licensing_block() -> dict:
    """The `licensing:` mapping from sandbox.local.yml (empty if unset)."""
    return dict((_local_yaml().get("licensing") or {}))


def _write_licensing_block(block: dict) -> None:
    """Persist the `licensing:` mapping back into sandbox.local.yml, preserving
    the rest of the file. Mirrors save_local_bridge_token's read-modify-write."""
    ensure_pyyaml()
    import yaml
    local = {}
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            local = yaml.safe_load(f) or {}
    if block:
        local["licensing"] = block
    else:
        local.pop("licensing", None)
    with CONFIG_LOCAL.open("w") as f:
        yaml.safe_dump(local, f, default_flow_style=False, sort_keys=False)
    try:
        CONFIG_LOCAL.chmod(0o600)  # secret store stays owner-only
    except OSError:
        pass


def set_license(family: str, key: str) -> None:
    """Store the license key for a family. Raises ValueError on unknown family
    or empty key. Never logs/echoes the value."""
    if family not in LICENSE_FAMILIES:
        raise ValueError(f"unknown license family '{family}' "
                         f"(expected: {', '.join(sorted(LICENSE_FAMILIES))})")
    key = (key or "").strip()
    if not key:
        raise ValueError("empty license key")
    block = _licensing_block()
    block[LICENSE_FAMILIES[family]] = key
    _write_licensing_block(block)


def clear_license(family: str | None = None) -> list:
    """Remove the key for a family (or all families when None). Also clears the
    Elementor primary designation when clearing elementor/all. Returns the list
    of families actually cleared."""
    block = _licensing_block()
    targets = [family] if family else list(LICENSE_FAMILIES)
    cleared = []
    for fam in targets:
        if fam not in LICENSE_FAMILIES:
            raise ValueError(f"unknown license family '{fam}'")
        if block.pop(LICENSE_FAMILIES[fam], None) is not None:
            cleared.append(fam)
        if fam == "elementor":
            block.pop("elementor_primary_instance", None)
            block.pop("elementor_primary_url", None)
    _write_licensing_block(block)
    return cleared


def get_license(family: str) -> str | None:
    """The stored key for a family, or None. Secret — callers must not echo it."""
    return _licensing_block().get(LICENSE_FAMILIES.get(family, "")) or None


def license_present(family: str) -> bool:
    return bool(get_license(family))


def _mask(key: str | None) -> str:
    """A safe, non-reversible hint for status output — never the full value."""
    if not key:
        return "(not set)"
    return f"set (…{key[-4:]})" if len(key) >= 4 else "set"


def elementor_primary() -> dict:
    """The auto-recorded Elementor primary designation (non-secret)."""
    b = _licensing_block()
    return {"instance": b.get("elementor_primary_instance"),
            "url": b.get("elementor_primary_url")}


def set_elementor_primary(instance: str, url: str) -> None:
    """Record the first-to-activate instance as the EL Pro primary (non-secret)."""
    block = _licensing_block()
    block["elementor_primary_instance"] = instance
    block["elementor_primary_url"] = url
    _write_licensing_block(block)


def license_status() -> dict:
    """Presence + masked hints + primary, for `sb license status`. No raw keys.
    WPDeveloper is not listed — its pro plugins are force-activated keylessly."""
    return {
        "elementor": _mask(get_license("elementor")),
        "elementor_primary": elementor_primary(),
    }
