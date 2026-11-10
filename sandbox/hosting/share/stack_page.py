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
import posixpath
import re
import secrets
import shlex
import subprocess
import sys
import time

from sandbox.hosting.share import MAX_STACK_HOSTS, SIGN_IN_SECONDS, ShareRefusal

STACK_SCHEMA = "sandbox.share-stack.v1"
KEY_FILE_PREFIX = "sandbox-share-"
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
_KID = re.compile(r"[0-9a-f]{32}")
_KEY = re.compile(r"[0-9a-f]{64}")
_URL_PATH = re.compile(r"/[A-Za-z0-9._~/-]*")


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


def with_key(stack: dict, link_id: str) -> dict:
    """Give the page's WordPress sign-in a fresh key, named for the link."""
    if stack.get("wordpress") is None:
        return stack
    return {**stack, "wordpress": {**stack["wordpress"], "kid": link_id[2:], "key": secrets.token_hex(32)}}


def gate_record(stack: dict, entry: str, link_id: str) -> dict:
    """The record the gate stores. It holds the verification key and nothing else secret."""
    wordpress = stack.get("wordpress")
    return {"schema": STACK_SCHEMA, "entry": entry, "link": link_id, "title": stack["title"],
            "hosts": stack["hosts"],
            "wordpress": ({"kid": wordpress["kid"], "key": wordpress["key"], "url": wordpress["url"]}
                          if wordpress else None),
            "sign_in": stack["sign_in"]}


def _php(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


_OPENER_SCRIPT = (
    "(function(){var m=location.hash.match(/^#([0-9]{1,12}\\.[A-Za-z0-9_-]{22,64}\\.[0-9a-f]{64})$/);"
    "var f=document.forms[0];history.replaceState(null,'',location.pathname+location.search);"
    "if(m){f.elements.token.value=m[1];f.submit();}else{f.hidden=true;"
    "document.getElementById('incomplete').hidden=false;}})();"
)
_OPENER_HASH = base64.b64encode(hashlib.sha256(_OPENER_SCRIPT.encode()).digest()).decode()


def render_key_plugin(kid: str, key: str, user: str, expires_at: int, *, now: float | None = None) -> str:
    """A WordPress mu-plugin that accepts single-use tokens signed with ``key`` until ``expires_at``.

    GET shows a page that posts the token from the fragment, so the token never reaches a
    request line. POST checks the signature, the token's own expiry (at most 10 minutes
    out), the key's expiry and a single-use nonce claimed by one ``INSERT IGNORE``.
    """
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
    if ( $now >= $until ) {{
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
    // inserts a row. add_site_option() is not enough: sitemeta has no unique key, and
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
    plugin = render_key_plugin(wordpress["kid"], wordpress["key"], wordpress["user"], expires_at)
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
    script = (f"n=0; for f in {shlex.quote(directory)}/{pattern}; do [ -e \"$f\" ] || continue; "
              "rm -f \"$f\" && n=$((n+1)); done; echo \"$n\"")
    service = shlex.quote(autologin.get("service") or found.validated["compose"]["service"])
    try:
        output = _remote_checked(found.entry, f"{_prefix(found)} exec -T {service} sh -c {shlex.quote(script)}",
                                 timeout=60)
    except (RuntimeError, subprocess.SubprocessError, OSError):
        raise ShareRefusal("share_failed", "the link is revoked, but its WordPress sign-in key could not be "
                                           "removed; retry the revoke") from None
    lines = [line for line in str(output or "").splitlines() if line.strip()]
    return int(lines[-1]) if lines and lines[-1].strip().isdigit() else 0
