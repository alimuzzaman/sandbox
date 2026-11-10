"""Token consumers for tailnet names, run only through the secrets broker.

Both children are fixed by Sandbox: the shipped ``cloudflare_child.py`` (run
with ``-I``) and ``lego``. The broker injects the token as
``CLOUDFLARE_DNS_API_TOKEN`` into a minimal environment, closes stdin, caps
and redacts the merged output, and audits names only. This module never
sees the token's value.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from sandbox.hosting.tailnet_names import cloudflare_child
from sandbox.hosting.tailnet_names.models import (
    CERTIFICATE_FAILED,
    DNS_FAILED,
    DNS_TOKEN_DESTINATION,
    SECRET_UNAVAILABLE,
    DnsResult,
    TailnetNameConfig,
    TailnetNameError,
)

DNS_TIMEOUT_SECONDS = 120
ISSUE_TIMEOUT_SECONDS = 900
TAIL_LINES = 6


def _tail(text: str) -> str:
    return " | ".join(line[-200:] for line in (text or "").splitlines()[-TAIL_LINES:])


class Broker:
    def __init__(self, service_factory: Callable[[], object], *, acme_root: Path,
                 executable: str = sys.executable) -> None:
        self._service_factory = service_factory
        self.acme_root = Path(acme_root)
        self.executable = executable

    def _run(self, config: TailnetNameConfig, argv: list[str], timeout: int) -> dict:
        from sandbox.secrets import SecretBrokerError
        try:
            payload = self._service_factory().run(
                config.token_source, config.token_key, argv,
                destination=DNS_TOKEN_DESTINATION, timeout_seconds=timeout, surface="cli")
        except SecretBrokerError as exc:
            raise TailnetNameError(
                SECRET_UNAVAILABLE,
                f"the secrets broker did not provide the DNS token ({exc.code})") from None
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise TailnetNameError(SECRET_UNAVAILABLE, "the secrets broker returned no result")
        return result

    def dns(self, config: TailnetNameConfig, request: dict) -> DnsResult:
        argv = [self.executable, "-I", cloudflare_child.__file__,
                json.dumps(request, sort_keys=True)]
        result = self._run(config, argv, DNS_TIMEOUT_SECONDS)
        lines = [line for line in str(result.get("output") or "").splitlines() if line.strip()]
        try:
            parsed = json.loads(lines[-1]) if lines else None
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            raise TailnetNameError(DNS_FAILED, "the DNS helper returned no result")
        return DnsResult.parse(parsed)

    def find_lego(self, config: TailnetNameConfig) -> str | None:
        if config.lego_path:
            path = config.lego_path
            return path if os.path.isfile(path) and os.access(path, os.X_OK) else None
        return shutil.which("lego")

    def issue(self, config: TailnetNameConfig, fqdn: str, csr_pem: str, *, lego: str) -> str:
        """Obtain a chain for the remote-made CSR; the key never comes here."""
        self.acme_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.acme_root, 0o700)
        csr_dir = self.acme_root / "csr"
        csr_dir.mkdir(exist_ok=True)
        csr_path = csr_dir / f"{fqdn}.csr"
        csr_path.write_text(csr_pem)
        os.chmod(csr_path, 0o600)
        certificates = self.acme_root / "certificates"
        for suffix in (".crt", ".issuer.crt", ".json"):
            (certificates / f"{fqdn}{suffix}").unlink(missing_ok=True)
        argv = [lego, "run", "--accept-tos", "--email", config.acme_email,
                "--server", config.acme_server, "--dns", "cloudflare",
                "--csr", str(csr_path), "--path", str(self.acme_root)]
        result = self._run(config, argv, ISSUE_TIMEOUT_SECONDS)
        if result.get("exit_code") != 0:
            raise TailnetNameError(
                CERTIFICATE_FAILED,
                f"lego did not issue a certificate ({result.get('termination')}): "
                + _tail(str(result.get("output") or "")))
        bundle = certificates / f"{fqdn}.crt"
        try:
            chain = bundle.read_text()
        except OSError:
            chain = ""
        if "-----BEGIN CERTIFICATE-----" not in chain:
            raise TailnetNameError(CERTIFICATE_FAILED, "lego finished without a certificate bundle")
        return chain
