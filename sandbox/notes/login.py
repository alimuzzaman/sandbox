"""Mint an Agent Notes API token through the dashboard and hand it to the secrets broker.

This works only from a machine the service trusts by its tailnet listener
(tailnet trust signs the visitor in; no password, no mail). The flow is the
dashboard's own forms: read the CSRF value, revoke every live token with our
name, create a new one and read it from the one-time page. The token goes
straight into ``SecretService.set``; it is never printed, logged or written
anywhere else.
"""

from __future__ import annotations

import html as _html
import http.cookiejar
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from .config import NotesService
from .errors import NotesError

_CSRF = re.compile(r'name="_csrf"\s+value="([^"]*)"')
_REVOKE_FORM = '<form method="post" action="/tokens/revoke"'
_ROW_NAME = re.compile(r'<span class="font-medium">(.*?)</span>', re.S)
_ID = re.compile(r'name="id"\s+value="(\d+)"')
_TOKEN = re.compile(r"\b(an_[A-Za-z0-9_-]{16,})\b")
_TOKEN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._@:-]{0,99}")


def default_token_name() -> str:
    host = socket.gethostname().split(".")[0] or "host"
    return "sb-notes-" + re.sub(r"[^A-Za-z0-9-]", "-", host)[:60]


def token_rows(page: str) -> list[tuple[str, int]]:
    """``(name, id)`` for every live token row on the dashboard."""
    chunks = page.split(_REVOKE_FORM)
    rows = []
    for before, form in zip(chunks, chunks[1:]):
        names = _ROW_NAME.findall(before)
        ident = _ID.search(form.split("</form>", 1)[0])
        if names and ident:
            rows.append((_html.unescape(names[-1]).strip(), int(ident.group(1))))
    return rows


class DashboardSession:
    def __init__(self, base_url: str, *, opener=None) -> None:
        self.base = base_url.rstrip("/")
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, path: str, form: dict | None = None) -> tuple[str, str]:
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"User-Agent": "sandbox-notes-login"})
        try:
            with self.opener.open(request, timeout=30) as response:
                return urllib.parse.urlparse(response.geturl()).path, response.read().decode(
                    "utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise NotesError("notes_login_failed",
                             f"{path} answered HTTP {exc.code}") from None
        except OSError as exc:
            raise NotesError("notes_unreachable", f"cannot reach {self.base}: {exc}") from None

    def dashboard(self) -> tuple[str, list[tuple[str, int]]]:
        path, page = self._open("/dashboard")
        if path != "/dashboard":
            raise NotesError(
                "notes_login_untrusted",
                f"{self.base} did not sign this machine in by tailnet trust (landed on {path}); "
                "run the login from a machine on the tailnet")
        csrf = _CSRF.search(page)
        if not csrf:
            raise NotesError("notes_login_failed", "the dashboard has no CSRF field")
        return _html.unescape(csrf.group(1)), token_rows(page)

    def revoke(self, csrf: str, ident: int) -> None:
        path, _ = self._open("/tokens/revoke", {"_csrf": csrf, "id": str(ident)})
        if path != "/dashboard":
            raise NotesError("notes_login_failed",
                             f"revoking the earlier token did not return to the dashboard ({path})")

    def create(self, csrf: str, name: str) -> str:
        _, page = self._open("/tokens/create", {"_csrf": csrf, "name": name})
        match = _TOKEN.search(page)
        if not match:
            raise NotesError("notes_login_failed", "the token page did not contain a token")
        return match.group(1)


def login(service: NotesService, *, token_name: str, store, verify=None, session=None) -> dict:
    """Mint, store and (optionally) verify. ``store(value)`` keeps the token; ``verify()`` checks it."""
    if not _TOKEN_NAME.fullmatch(token_name):
        raise NotesError("notes_input_invalid", "token names use letters, digits, ' ._@:-' (max 100)")
    session = session or DashboardSession(service.url)
    csrf, rows = session.dashboard()
    revoked = [ident for name, ident in rows if name == token_name]
    for ident in revoked:
        session.revoke(csrf, ident)
    token = session.create(csrf, token_name)
    store(token)
    del token
    result = {"remote": service.remote, "url": service.url, "token_name": token_name,
              "revoked_previous": len(revoked),
              "stored": {"source": service.token_source, "key": service.token_key},
              "verified": None}
    if verify is not None:
        verify()
        result["verified"] = True
    return result
