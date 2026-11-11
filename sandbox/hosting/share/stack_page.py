"""The shareable stack page (spec 081, User Story 5).

A stack link opens a read-only page, served by the stack target's own gate, that lists
the stack's servers with their health and fresh sign-in links. Two kinds of sign-in:

* WordPress admin, for a stack target that declares ``autologin`` (user and
  container_path) and is not private. Minting installs a per-link mu-plugin holding a
  random verification key; the gate holds the same key and signs a single-use token for
  each view. The key file is named for the link and its expiry: revoking the link
  deletes it, and an expired one is inert and removed at the next mint.
* Login-service entries named in ``share.stack_sign_in`` (a Hub QA sign-in), minted by
  the gate itself in its own state.

No sign-in token is ever stored: the key verifies, and each token's nonce is recorded
as used by WordPress itself.
"""
from __future__ import annotations

import base64
import hashlib
import json
import copy
import posixpath
import re
import secrets
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import TypedDict
from urllib.parse import urlsplit

from sandbox.hosting.share import MAX_STACK_HOSTS, SIGN_IN_SECONDS, ShareRefusal

STACK_SCHEMA = "sandbox.share-stack.v1"
KEY_FILE_PREFIX = "sandbox-share-"
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
# The link id without "l-": 12 hex, or 32 for an old-form link (LEGACY(081 short links)).
_KID = re.compile(r"[0-9a-f]{12}|[0-9a-f]{32}")
_KEY = re.compile(r"[0-9a-f]{64}")
_URL_PATH = re.compile(r"/[A-Za-z0-9._~/-]*")


STACK_SCHEMA_V2 = "sandbox.share-stack.v2"
MAX_STACK_PAGE_BYTES = 32768
MAX_COMPANIONS = 4


class HostRowV2(TypedDict):
    hostname: str
    role: str
    probe: str
    commit: str | None
    state: str
    access: str


@dataclass(frozen=True, order=True)
class CoverageGap:
    remote: str
    slug: str
    hostname: str
    code: str
    guidance: str


@dataclass(frozen=True)
class TargetContext:
    """Prepared snapshot. Orchestration resolves direct uses before coverage.

    linked_sites replaces record.sites only when explicitly supplied. Each linked
    row needs exact share.linked_access intent; record access alone grants nothing.
    commit is delivered evidence, never the requested branch or pending revision.
    """
    remote: str
    slug: str
    validated: dict
    declaration: dict | None
    record: dict
    linked_sites: tuple[dict, ...] | None = None
    commit: str | None = None
    state: str = "unknown"


@dataclass(frozen=True)
class CoverageResult:
    hosts: tuple[HostRowV2, ...]
    gaps: tuple[CoverageGap, ...]

    def require_complete(self) -> None:
        if self.gaps:
            raise ShareRefusal("share_coverage_incomplete", "; ".join(
                f"{g.remote}/{g.slug}/{g.hostname or '-'}: {g.code}: {g.guidance}"
                for g in self.gaps))


def _role(hostname: str) -> str:
    label = hostname.split(".", 1)[0]
    return label[:32] if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", label[:32]) else "server"


def _covered(hostname: str, suffixes: list[str]) -> bool:
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes)


class _NoPrompt:
    """``resolve_email`` prompts only on a terminal; a share never prompts."""

    @staticmethod
    def isatty() -> bool:
        return False


def build(found, declaration: dict, *, request_email: str | None) -> dict:
    """What the controller knows about the page before the link exists (no key yet)."""
    from sandbox.core import _hosting as hosting
    from sandbox.hosting import login_service as login
    record = found.stack or {}
    slug = str(record.get("slug") or "")
    share_block = login.share_declaration(declaration) or {}
    gate = login.gate_hostnames(declaration)
    suffixes = declaration["allowed_host_suffixes"]
    hosts, seen = [], set()
    own = [str(name).lower() for name in record.get("hostnames") or []]
    sites = [str((item or {}).get("hostname") or "").lower() for item in record.get("sites") or []
             if isinstance(item, dict)]
    for hostname, probe in [*((name, "internal") for name in own), *((name, "public") for name in sites)]:
        if hostname in seen or hostname in gate or not _HOST.fullmatch(hostname):
            continue
        if not _covered(hostname, suffixes):
            continue
        seen.add(hostname)
        hosts.append({"hostname": hostname, "role": _role(hostname), "probe": probe})
    if len(hosts) > MAX_STACK_HOSTS:
        hosts = hosts[:MAX_STACK_HOSTS]
    notes = []
    wordpress = None
    autologin = found.validated.get("autologin")
    if autologin and login.is_private(declaration):
        notes.append("WordPress autologin is left out: the target is private, so its primary "
                     "hostname answers 404")
    elif autologin:
        primary = next(route["hostname"] for route in found.validated["routes"] if route.get("primary"))
        path = autologin.get("request_path") or "/"
        if not _URL_PATH.fullmatch(path):
            raise ShareRefusal("share_failed", "autologin.request_path cannot carry a share sign-in")
        wordpress = {"user": autologin["user"], "container_path": autologin["container_path"],
                     "service": autologin.get("service") or found.validated["compose"]["service"],
                     "url": f"https://{primary}{path}"}
    sign_in = []
    saved = (hosting.load_host_state().get("hosts", {}).get(
        hosting.state_key(found.remote, found.validated)) or {}).get(login.STATE_KEY)
    for name in share_block.get("stack_sign_in") or []:
        config = declaration["entries"][name]
        if config["mode"] == "magic_link":
            chosen = login.resolve_email(config, requested=request_email, replace_default=False,
                                         saved=saved, stdin=_NoPrompt(), stderr=sys.stderr)
            sign_in.append({"entry": name, "email": chosen.address})
        else:
            sign_in.append({"entry": name})
    if request_email is not None and not any("email" in item for item in sign_in):
        raise ShareRefusal("share_options_conflict", "--email applies only to a magic_link sign-in entry")
    return {"title": slug, "hosts": hosts, "wordpress": wordpress, "sign_in": sign_in, "notes": notes}


def coverage(context: TargetContext) -> CoverageResult:
    """Enumerate every recorded hostname without effects, filtering or truncation.

    The share gate itself is infrastructure, accounted for by its declaration.
    Other gates need a listed action. Linked rows require exact author intent even
    when their hostname duplicates an own route.
    """
    from sandbox.hosting import login_service as login
    declaration = context.declaration or {}
    share = declaration.get("share") or {}
    entries = declaration.get("entries") or {}
    suffixes = declaration.get("allowed_host_suffixes") or []
    mapping = share.get("route_sign_in") or {}
    linked_access = {}
    gaps, hosts = [], {}

    def gap(host, code, guidance):
        gaps.append(CoverageGap(context.remote, context.slug, str(host), code, guidance))

    try:
        linked_access = login.validate_linked_access(share.get("linked_access", {}), suffixes)
    except login.LoginServiceError as exc:
        gap("", "linked_access_invalid", exc.message)
    if not share:
        gap("", "share_missing", "declare share with a served gate hostname and apply")
    if context.validated.get("basic_auth"):
        gap("", "basic_auth", "BasicAuth cannot be supplied by this page; remove it or provide supported access")
    wp = context.validated.get("autologin")
    if wp and login.is_private(declaration):
        gap("", "private_wordpress", "private WordPress cannot be reached by a public stack grant")
    routes = context.validated.get("routes") or []
    try:
        login.validate_route_sign_in(share, entries, routes, wordpress=wp)
    except login.LoginServiceError as exc:
        gap("", "route_mapping_invalid", exc.message)
    if context.record.get("ports", {}).get("mailpit") is not None and not share.get("inbox_sign_in"):
        gap("", "inbox_uncovered", "set share.inbox_sign_in to a listed inbox_gate entry and apply")
    sources = [(route.get("hostname"), "internal", None) for route in routes]
    sources += [(host, "internal", None) for host in context.record.get("hostnames") or []]
    linked = context.linked_sites if context.linked_sites is not None else context.record.get("sites") or []
    sources += [(row.get("hostname"), "public", row) if isinstance(row, dict)
                else ("", "public", {}) for row in linked]
    recorded_links = {row.get("hostname") for row in linked
                      if isinstance(row, dict) and isinstance(row.get("hostname"), str)}
    for host in sorted(set(linked_access) - recorded_links):
        gap(host, "linked_access_unrecorded", "remove this share.linked_access key or record that exact linked site")
    gates = {entry.get("hostname"): (name, entry) for name, entry in entries.items()}
    for raw, probe, linked_row in sources:
        host = raw if isinstance(raw, str) else ""
        if len(host) > 253 or not _HOST.fullmatch(host):
            gap(host, "host_invalid", "record an exact lowercase DNS hostname; wildcards are unsupported")
            continue
        if not _covered(host, suffixes):
            gap(host, "host_suffix", "add this hostname to allowed_host_suffixes and apply")
        access = mapping.get(host, "open")
        commit, state = context.commit, context.state
        if linked_row is not None:
            # Manifest intent applies only to the exact recorded link. A legacy
            # access=open field is neither a declaration nor authentication proof.
            access = linked_access.get(host, "unknown")
            if access != "open":
                gap(host, "linked_access_unknown", "declare share.linked_access for this exact recorded hostname as open only if its normal route is anonymous")
            if (linked_row.get("access") not in (None, "open")
                    or any(linked_row.get(flag) for flag in
                           ("basic_auth", "private", "protected", "requires_auth", "inaccessible"))):
                gap(host, "linked_access_unsupported", "protected linked apps are unsupported; remove known restrictions before declaring share.linked_access open")
            own_commit = linked_row.get("commit")
            commit = (own_commit if isinstance(own_commit, str)
                      and re.fullmatch(r"[0-9a-f]{40}", own_commit) else None)
            own_state = linked_row.get("state")
            state = (own_state if isinstance(own_state, str) and own_state in
                     {"unknown", "serving", "redirecting", "applying", "failed", "instance_not_found"}
                     else "unknown")
        if host in gates:
            name, entry = gates[host]
            if name == "share" and host == share.get("hostname"):
                continue
            if entry.get("mode") == "inbox_gate" and name in share.get("stack_sign_in", []):
                access = name
            elif entry.get("mode") == "magic_link" and name in share.get("stack_sign_in", []):
                # The action destination is represented by its mapped route, not the opener host.
                continue
            else:
                gap(host, "gate_uncovered", "list a supported magic_link or inbox_gate action; site_gate is unsupported")
        if login.is_private(declaration) and host not in gates:
            gap(host, "private_route", "private non-gate routes answer 404; configure supported gate access")
        row = {"hostname": host, "role": _role(host), "probe": probe,
               "commit": commit, "state": state, "access": access}
        if linked_row is not None:
            hosts[host] = row
        else:
            hosts.setdefault(host, row)
    if len(hosts) > MAX_STACK_HOSTS:
        gap("", "host_overflow", f"page exceeds {MAX_STACK_HOSTS} hosts; reduce recorded routes")
    return CoverageResult(tuple(hosts[host] for host in sorted(hosts)), tuple(sorted(set(gaps))))


def coverage_all(parent: TargetContext, companions=()) -> tuple[CoverageGap, ...]:
    """Collect parent/direct gaps before orchestration creates any keys or grants."""
    companions = tuple(companions)
    gaps = list(coverage(parent).gaps)
    if len(companions) > MAX_COMPANIONS:
        gaps.append(CoverageGap(parent.remote, parent.slug, "", "companion_overflow",
                                "at most four direct uses can be shared"))
    seen = {(parent.remote, parent.slug)}
    for target in companions:
        gaps.extend(coverage(target).gaps)
        identity = (target.remote, target.slug)
        if identity in seen:
            gaps.append(CoverageGap(target.remote, target.slug, "", "companion_duplicate",
                                    "resolve unique direct uses without the parent itself"))
        seen.add(identity)
        if target.remote != parent.remote:
            gaps.append(CoverageGap(target.remote, target.slug, "", "remote_mismatch",
                                    "direct companion must be on the parent's recorded remote"))
    return tuple(sorted(set(gaps)))


def prepare_pages(parent: TargetContext, companions=(), *, sign_ins=None) -> tuple[dict, ...]:
    """Preflight all same-remote target pages, aggregating diagnostics before effects.

    Returns parent then direct pages with no companion credentials. Orchestration
    adds descriptors from the SAME preallocated MintedLink objects, then calls
    build_v2 again to bound the final parent payload before key installation/mint.
    sign_ins maps (remote, slug) to resolved entry/email rows. No state is read.
    """
    companions = tuple(companions)
    gaps = list(coverage_all(parent, companions))
    pages = []
    for context in (parent, *companions):
        if coverage(context).gaps:
            continue
        try:
            pages.append(build_v2(context, sign_in=(sign_ins or {}).get(
                (context.remote, context.slug), ())))
        except ShareRefusal as exc:
            gaps.append(CoverageGap(context.remote, context.slug, "", exc.code, exc.message))
    CoverageResult((), tuple(sorted(set(gaps)))).require_complete()
    return tuple(pages)


def build_v2(context: TargetContext, *, sign_in=(), companions=()) -> dict:
    """Pure page preparation; callers resolve allowed email addresses before this call.

    No keys, tokens, state reads, companion resolution or minting happen here.
    Preflight every target with coverage_all before allocating grant material.
    """
    result = coverage(context)
    result.require_complete()
    declaration = context.declaration or {}
    share = declaration["share"]
    listed = share.get("stack_sign_in") or []
    supplied = {row.get("entry"): row for row in sign_in if isinstance(row, dict)}
    if len(listed) > 16 or any(name not in declaration.get("entries", {}) for name in listed):
        raise ShareRefusal("share_page_invalid", "sign-in entries must be declared and bounded")
    for child in companions:
        if not isinstance(child, dict) or not _covered(str(child.get("hostname", "")), declaration["allowed_host_suffixes"]):
            raise ShareRefusal("share_page_invalid", "companion hostname must be covered by parent allowed_host_suffixes")
    actions = []
    from sandbox.hosting import login_service as login
    for name in listed:
        config = declaration["entries"][name]
        row = {"entry": name}
        if config["mode"] == "magic_link":
            email = supplied.get(name, {}).get("email")
            try:
                row["email"] = login.validate_email(email)
            except login.LoginServiceError:
                raise ShareRefusal("share_options_conflict", "resolve the magic_link email before page preparation") from None
            if row["email"].casefold() in config["magic_link"].get("denied_emails", []):
                raise ShareRefusal("share_options_conflict", "the selected sign-in email is denied")
        actions.append(row)
    wp = context.validated.get("autologin")
    wordpress = None
    if wp:
        primary = next((r["hostname"] for r in context.validated["routes"] if r.get("primary")), None)
        path = wp.get("request_path") or "/"
        if not primary or not _URL_PATH.fullmatch(path) or ".." in path.split("/"):
            raise ShareRefusal("share_coverage_incomplete", "WordPress needs an exact primary hostname and safe request_path")
        wordpress = {"user": wp["user"], "container_path": wp["container_path"],
                     "service": wp.get("service") or context.validated["compose"]["service"],
                     "url": f"https://{primary}{path}", "claim_origin": f"https://{share['hostname']}"}
    inbox = share.get("inbox_sign_in")
    prepared = {"schema": STACK_SCHEMA_V2, "title": context.slug,
                "hosts": list(result.hosts), "notes": [], "wordpress": wordpress,
                "sign_in": actions, "inbox": {"entry": inbox} if inbox else None,
                "companions": copy.deepcopy(list(companions))}
    # Fixed-width stand-ins validate the exact eventual wire size without generating a key.
    trial = copy.deepcopy(prepared)
    if trial["wordpress"]:
        trial["wordpress"].update(kid="0" * 32, key="0" * 64)
    gate_record(trial, "share", "l-" + "0" * 32)
    return prepared


def with_key(stack: dict, link_id: str) -> dict:
    """Give the page's WordPress sign-in a fresh key, named for the link."""
    if stack.get("wordpress") is None:
        return stack
    return {**stack, "wordpress": {**stack["wordpress"], "kid": link_id[2:], "key": secrets.token_hex(32)}}


def gate_record(stack: dict, entry: str, link_id: str) -> dict:
    """The record the gate stores. It holds the verification key and nothing else secret."""
    wordpress = stack.get("wordpress")
    if stack.get("schema") == STACK_SCHEMA_V2:
        record = {"schema": STACK_SCHEMA_V2, "entry": entry, "link": link_id,
                  "title": stack["title"], "hosts": copy.deepcopy(stack["hosts"]),
                  "notes": list(stack["notes"]), "sign_in": copy.deepcopy(stack["sign_in"]),
                  "inbox": copy.deepcopy(stack["inbox"]),
                  "companions": copy.deepcopy(stack["companions"]),
                  "wordpress": ({k: wordpress[k] for k in ("kid", "key", "url", "claim_origin")}
                                if wordpress else None)}
        if not valid_stack_v2(record):
            raise ShareRefusal("share_page_invalid", "v2 page is invalid or exceeds its serialized size limit")
        return record
    return {"schema": STACK_SCHEMA, "entry": entry, "link": link_id, "title": stack["title"],
            "hosts": stack["hosts"],
            "wordpress": ({"kid": wordpress["kid"], "key": wordpress["key"], "url": wordpress["url"]}
                          if wordpress else None),
            "sign_in": stack["sign_in"]}


def valid_stack_v2(record: object) -> bool:
    """Exact private gate wire contract; stdlib-only rules mirrored by runtime owner.

    This validator must never be used to sanitize public list/audit output: the
    record deliberately contains keys and companion bearer credentials.
    """
    from sandbox.hosting.share import LINK_ID_RE, short_link_id
    from sandbox.hosting import login_service as login

    def text(value, limit):
        return (isinstance(value, str) and 0 < len(value) <= limit
                and all(ord(c) >= 32 and ord(c) != 127 for c in value))

    def host(value):
        return isinstance(value, str) and len(value) <= 253 and bool(_HOST.fullmatch(value))

    def origin(value):
        return (isinstance(value, str) and value.startswith("https://")
                and host(value[8:]))

    def notes(value):
        return isinstance(value, list) and len(value) <= 16 and all(text(v, 512) for v in value)

    def rows(value, actions, wp):
        if not isinstance(value, list) or len(value) > MAX_STACK_HOSTS:
            return False
        seen = set()
        for row in value:
            if not isinstance(row, dict) or set(row) != {"hostname", "role", "probe", "commit", "state", "access"}:
                return False
            if (not host(row["hostname"]) or row["hostname"] in seen
                    or not text(row["role"], 32) or row["probe"] not in ("internal", "public")
                    or row["state"] not in ("unknown", "serving", "redirecting", "applying", "failed", "instance_not_found")
                    or (row["commit"] is not None and (not isinstance(row["commit"], str)
                        or not re.fullmatch(r"[0-9a-f]{40}", row["commit"])))
                    or not isinstance(row["access"], str)
                    or row["access"] not in {"open", *actions, *(["$wordpress"] if wp else [])}):
                return False
            seen.add(row["hostname"])
        return True

    try:
        if not isinstance(record, dict) or set(record) != {
                "schema", "entry", "link", "title", "hosts", "notes", "wordpress", "sign_in", "inbox", "companions"}:
            return False
        if len(json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) > MAX_STACK_PAGE_BYTES:
            return False
        if (record["schema"] != STACK_SCHEMA_V2 or not isinstance(record["entry"], str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", record["entry"])
                or not isinstance(record["link"], str) or not LINK_ID_RE.fullmatch(record["link"])
                or not text(record["title"], 63) or not notes(record["notes"])):
            return False
        actions = set()
        if not isinstance(record["sign_in"], list) or len(record["sign_in"]) > 16:
            return False
        for row in record["sign_in"]:
            if not isinstance(row, dict) or set(row) not in ({"entry"}, {"entry", "email"}):
                return False
            name = row["entry"]
            if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name)
                    or name in actions or name == "share"):
                return False
            if "email" in row:
                login.validate_email(row["email"])
            actions.add(name)
        wp = record["wordpress"]
        if wp is not None:
            if (not isinstance(wp, dict) or set(wp) != {"kid", "key", "url", "claim_origin"}
                    or not isinstance(wp["kid"], str) or not _KID.fullmatch(wp["kid"])
                    or wp["kid"] != record["link"][2:]
                    or not isinstance(wp["key"], str) or not _KEY.fullmatch(wp["key"])
                    or not origin(wp["claim_origin"]) or not isinstance(wp["url"], str)):
                return False
            parsed = urlsplit(wp["url"])
            if (not origin("https://" + parsed.netloc) or parsed.scheme != "https"
                    or parsed.query or parsed.fragment or not _URL_PATH.fullmatch(parsed.path)
                    or ".." in parsed.path.split("/")):
                return False
        if not rows(record["hosts"], actions, wp is not None):
            return False
        inbox = record["inbox"]
        if inbox is not None and (not isinstance(inbox, dict) or set(inbox) != {"entry"}
                                  or not isinstance(inbox["entry"], str) or inbox["entry"] not in actions
                                  or any(row["entry"] == inbox["entry"] and "email" in row
                                         for row in record["sign_in"])):
            return False
        companions = record["companions"]
        if not isinstance(companions, list) or len(companions) > MAX_COMPANIONS:
            return False
        seen = set()
        for row in companions:
            if not isinstance(row, dict) or set(row) != {"slug", "target", "hostname", "link_id", "expires_at", "token", "title", "hosts", "notes"}:
                return False
            if (not isinstance(row["slug"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", row["slug"])
                    or not isinstance(row["target"], str)
                    or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,191}", row["target"])
                    or any(p in ("", ".", "..") for p in row["target"].split("/"))
                    or not host(row["hostname"]) or row["target"] in seen
                    or not isinstance(row["token"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{22}", row["token"])
                    or row["link_id"] != short_link_id(row["token"])
                    or type(row["expires_at"]) is not int or not 0 < row["expires_at"] < 10**12
                    or not text(row["title"], 63) or not notes(row["notes"])):
                return False
            # Companion rows are display metadata. Their own page owns actions.
            display_actions = {r.get("access") for r in row["hosts"] if isinstance(r, dict)
                               and isinstance(r.get("access"), str)} if isinstance(row["hosts"], list) else set()
            if (any(action not in ("open", "$wordpress") and not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", action)
                    for action in display_actions) or not rows(row["hosts"], display_actions, True)):
                return False
            seen.add(row["target"])
        return True
    except (TypeError, ValueError, KeyError, OverflowError, login.LoginServiceError):
        return False


def _php(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


_OPENER_SCRIPT = (
    "(function(){var m=location.hash.match(/^#([0-9]{1,12}\\.[A-Za-z0-9_-]{22,64}\\.[0-9a-f]{64})$/);"
    "var f=document.forms[0];history.replaceState(null,'',location.pathname+location.search);"
    "if(m){f.elements.token.value=m[1];f.submit();}else{f.hidden=true;"
    "document.getElementById('incomplete').hidden=false;}})();"
)
_OPENER_HASH = base64.b64encode(hashlib.sha256(_OPENER_SCRIPT.encode()).digest()).decode()


def render_key_plugin(kid: str, key: str, user: str, expires_at: int, *, now: float | None = None,
                      claim_origin: str | None = None) -> str:
    """A WordPress mu-plugin that accepts single-use tokens signed with ``key`` until ``expires_at``.

    GET shows a page that posts the token from the fragment, so the token never reaches a
    request line. POST checks the signature, the token's own expiry (at most 10 minutes
    out), the key's expiry and a single-use nonce claimed by one ``INSERT IGNORE``.
    """
    if claim_origin is not None:
        from sandbox.hosting.share.wordpress import render_claim_plugin
        return render_claim_plugin(kid, key, user, expires_at, claim_origin, now=now)
    if not _KID.fullmatch(kid) or not _KEY.fullmatch(key):
        raise ShareRefusal("share_failed", "a share key must be 128-bit id and 256-bit key in hex")
    if int(expires_at) <= int(time.time() if now is None else now):
        raise ShareRefusal("share_failed", "a share key's expiry must be in the future")
    if not user or len(user) > 60 or any(ord(char) < 33 or ord(char) > 126 for char in user):
        raise ShareRefusal("share_failed", "autologin.user must be a printable WordPress login name")
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex, nofollow"><title>Signing in</title></head>'
            '<body><main><h1>Signing in</h1><form method="post"><input type="hidden" name="token">'
            '<button>Continue</button></form><p id="incomplete" hidden>This sign-in link is incomplete. '
            'Open it from the stack page again.</p>'
            f'<script>{_OPENER_SCRIPT}</script></main></body></html>')
    policy = (f"default-src 'none'; script-src 'sha256-{_OPENER_HASH}'; form-action 'self'; "
              "base-uri 'none'; frame-ancestors 'none'")
    return f"""<?php
/** Generated by Sandbox share links (spec 081). It holds a verification key, never a login token. */
defined( 'ABSPATH' ) || exit;

add_action( 'init', static function () {{
    $kid = {_php(kid)};
    if ( ! isset( $_GET['sandbox_share_login'] ) || (string) $_GET['sandbox_share_login'] !== $kid ) {{
        return;
    }}
    nocache_headers();
    header( 'X-Robots-Tag: noindex, nofollow, noarchive' );
    header( 'Referrer-Policy: no-referrer' );
    $until = {int(expires_at)};
    $now = time();
    // One blog only, so one options table holds every claimed nonce: on a multisite the
    // same token cannot be spent again under another blog or another network's main site.
    if ( $now >= $until || ( is_multisite()
            && get_current_blog_id() !== (int) get_main_site_id( get_main_network_id() ) ) ) {{
        status_header( 404 );
        exit;
    }}
    if ( 'POST' !== ( $_SERVER['REQUEST_METHOD'] ?? 'GET' ) ) {{
        header( 'Content-Type: text/html; charset=utf-8' );
        header( {_php("Content-Security-Policy: " + policy)} );
        echo {_php(page)};
        exit;
    }}
    $origin = $_SERVER['HTTP_ORIGIN'] ?? null;
    if ( null !== $origin && 'null' !== $origin && $origin !== 'https://' . ( $_SERVER['HTTP_HOST'] ?? '' ) ) {{
        status_header( 403 );
        exit;
    }}
    $token = isset( $_POST['token'] ) ? (string) wp_unslash( $_POST['token'] ) : '';
    if ( ! preg_match( '/\\A([0-9]{{1,12}})\\.([A-Za-z0-9_-]{{22,64}})\\.([0-9a-f]{{64}})\\z/', $token, $m ) ) {{
        status_header( 403 );
        wp_die( 'This Sandbox sign-in link is invalid.' );
    }}
    $exp = (int) $m[1];
    if ( $exp <= $now || $exp > $now + {SIGN_IN_SECONDS} || $exp > $until ) {{
        status_header( 403 );
        wp_die( 'This Sandbox sign-in link has expired. Reload the stack page for a new one.' );
    }}
    $mac = hash_hmac( 'sha256', 'sandbox-share-login.v1|' . $kid . '|' . $m[1] . '|' . $m[2], hex2bin( {_php(key)} ) );
    if ( ! hash_equals( $mac, $m[3] ) ) {{
        status_header( 403 );
        wp_die( 'This Sandbox sign-in link is invalid.' );
    }}
    // The claim is one INSERT IGNORE on this site's options table, whose option_name is a
    // UNIQUE key on single sites and on every multisite blog: of concurrent requests, one
    // inserts a row. Network options are not enough (sitemeta has no unique key), and
    // add_option() reports a changed value as success.
    global $wpdb;
    $used = 'sandbox_share_n_' . substr( hash( 'sha256', $kid . '|' . $m[2] ), 0, 40 );
    $claimed = $wpdb->query( $wpdb->prepare(
        'INSERT IGNORE INTO ' . $wpdb->options . " (option_name, option_value, autoload) VALUES (%s, %s, 'no')",
        $used, (string) $exp
    ) );
    if ( 1 !== $claimed ) {{
        status_header( 410 );
        wp_die( 'This Sandbox sign-in link has already been used.' );
    }}
    $user = get_user_by( 'login', {_php(user)} );
    if ( ! $user ) {{
        status_header( 403 );
        wp_die( 'The configured Sandbox login user is unavailable.' );
    }}
    wp_set_current_user( $user->ID );
    wp_set_auth_cookie( $user->ID, false, is_ssl() );
    wp_safe_redirect( is_multisite() ? network_admin_url() : admin_url() );
    exit;
}}, 1 );
"""


def _prefix(found) -> str:
    from sandbox.commands.hosting import _compose_prefix
    import sandbox.core._remote as remote
    home = remote.resolve_sandbox_home(found.entry)
    validated = found.validated
    runtime_dir = f"{home}/runtime/hosts/{validated['project']}/{validated['environment']}"
    return _compose_prefix(validated, f"{home}/deploy-src/hosts/{validated['project']}",
                           f"{runtime_dir}/compose.override.yml", f"{runtime_dir}/environment.env")


def key_directory(container_path: str) -> str:
    return posixpath.dirname(container_path)


def install_wordpress_key(found, stack: dict, expires_at: int) -> None:
    """Write this link's key file into the WordPress container, and drop expired ones.

    The plugin travels on SSH stdin: argv is visible to process listings.
    """
    wordpress = stack.get("wordpress")
    if not wordpress:
        return
    from sandbox.commands.hosting import _remote_checked
    plugin = render_key_plugin(wordpress["kid"], wordpress["key"], wordpress["user"], expires_at,
                               **({"claim_origin": wordpress["claim_origin"]}
                                  if "claim_origin" in wordpress else {}))
    directory = key_directory(wordpress["container_path"])
    target = posixpath.join(directory, f"{KEY_FILE_PREFIX}{int(expires_at)}-{wordpress['kid']}.php")
    script = (
        f"set -eu; umask 022; d={shlex.quote(directory)}; mkdir -p \"$d\"; now=$(date +%s); "
        f"for f in \"$d\"/{KEY_FILE_PREFIX}*-*.php; do [ -e \"$f\" ] || continue; "
        f"b=${{f##*/{KEY_FILE_PREFIX}}}; e=${{b%%-*}}; "
        "case \"$e\" in ''|*[!0-9]*) continue;; esac; "
        "[ \"$e\" -le \"$now\" ] && rm -f \"$f\"; done; "
        f"t={shlex.quote(target)}; tmp=\"$t.tmp.$$\"; cat > \"$tmp\"; chmod 0644 \"$tmp\"; mv -f \"$tmp\" \"$t\""
    )
    service = shlex.quote(wordpress["service"])
    _remote_checked(found.entry, f"{_prefix(found)} exec -T {service} sh -c {shlex.quote(script)}",
                    timeout=60, input_data=plugin)


def remove_wordpress_keys(found, link_id: str) -> int:
    """Delete the key file of one stack link, or every key file for ``all``."""
    autologin = found.validated.get("autologin")
    if not autologin:
        return 0
    from sandbox.commands.hosting import _remote_checked
    directory = key_directory(autologin["container_path"])
    pattern = f"{KEY_FILE_PREFIX}*-*.php" if link_id == "all" else f"{KEY_FILE_PREFIX}*-{link_id[2:]}.php"
    if link_id != "all" and not _KID.fullmatch(link_id[2:]):
        return 0
    # Any file that survives fails the whole command, so a revoke never reports a key gone
    # that still signs people in.
    script = (f"n=0; for f in {shlex.quote(directory)}/{pattern}; do [ -e \"$f\" ] || continue; "
              "rm -f \"$f\" || exit 1; [ ! -e \"$f\" ] || exit 1; n=$((n+1)); done; echo \"sandbox-keys=$n\"")
    service = shlex.quote(autologin.get("service") or found.validated["compose"]["service"])
    try:
        output = _remote_checked(found.entry, f"{_prefix(found)} exec -T {service} sh -c {shlex.quote(script)}",
                                 timeout=60)
    except (RuntimeError, subprocess.SubprocessError, OSError):
        raise ShareRefusal("share_failed", "the link is revoked, but its WordPress sign-in key could not be "
                                           "removed; retry the revoke") from None
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    found = re.fullmatch(r"sandbox-keys=([0-9]{1,4})", lines[-1]) if lines else None
    if found is None:
        raise ShareRefusal("share_failed", "the link is revoked, but removing its WordPress sign-in key gave "
                                           "no readable answer; retry the revoke")
    return int(found.group(1))
