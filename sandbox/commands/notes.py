"""``sb notes`` and ``sb mockups``: publish to an Agent Notes service with a broker-held token."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from sandbox.notes import config as notes_config
from sandbox.notes.errors import NotesError
from sandbox.registry import CommandSpec, register_specs

# Tests replace this to run the commands against a fake service.
_transport_factory = None


def _common(parser) -> None:
    parser.add_argument("--remote", help="notes service to use (default: notes.default_remote)")
    parser.add_argument("--json", action="store_true", help="print one JSON object")


def configure_notes(parser) -> None:
    parser.description = ("Publish markdown or HTML notes to the Agent Notes service. "
                          "The token stays in the secrets broker; see docs/agent-notes.md.")
    actions = parser.add_subparsers(dest="notes_action", required=True, metavar="ACTION")

    p = actions.add_parser("login", help="mint a token over tailnet trust and store it in the broker")
    _common(p)
    p.add_argument("--url", help="service origin; recorded in sandbox.local.yml for --remote")
    p.add_argument("--name", dest="token_name", help="token name (default sb-notes-<host>); "
                   "a live token with this name is revoked first")
    p.add_argument("--no-verify", action="store_true", help="skip the test call after storing")

    p = actions.add_parser("config", help="show or record which service a remote name means")
    _common(p)
    p.add_argument("--url", help="record this https origin for --remote")
    p.add_argument("--default", action="store_true", help="make --remote the default")

    p = actions.add_parser("create", help="publish a file as a new note")
    _common(p)
    p.add_argument("--file", required=True)
    p.add_argument("--title")
    p.add_argument("--html", action="store_true", help="the file is one complete HTML document")
    p.add_argument("--public", action="store_true",
                   help="anyone on the internet can open it; only for notes with nothing private")

    p = actions.add_parser("update", help="replace a note's content; the URL stays")
    _common(p)
    p.add_argument("slug")
    p.add_argument("--file", required=True)
    p.add_argument("--title")

    for name, text in (("get", "print one note"), ("delete", "delete one note")):
        p = actions.add_parser(name, help=text)
        _common(p)
        p.add_argument("slug")

    p = actions.add_parser("list", help="list your notes")
    _common(p)

    p = actions.add_parser("sync", help="publish every .md file in a folder, rerunnable")
    _common(p)
    p.add_argument("directory")
    p.add_argument("--prune", action="store_true",
                   help="delete notes whose file is gone (default: report them)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--adopt", action="store_true",
                       help="for an unmapped file, take over the matching existing note even "
                            "if its content differs (default: only an identical one)")
    group.add_argument("--create-duplicates", action="store_true",
                       help="for an unmapped file, always publish a new note")

    p = actions.add_parser("upload", help="attach an image to a note")
    _common(p)
    p.add_argument("slug")
    p.add_argument("file")

    p = actions.add_parser("share", help="make or revoke expiring public share links")
    _common(p)
    p.add_argument("slug")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--ttl", help="lifetime, e.g. 24h or 7d")
    group.add_argument("--revoke", action="store_true", help="revoke every active link")


def configure_mockups(parser) -> None:
    parser.description = ("Publish a folder (index.html plus its files) as one HTML note "
                          "with image assets. See docs/agent-notes.md.")
    actions = parser.add_subparsers(dest="mockups_action", required=True, metavar="ACTION")

    p = actions.add_parser("publish", help="publish a new mockup; refuses an existing name")
    _common(p)
    p.add_argument("directory")
    p.add_argument("--name", required=True)
    p.add_argument("--title")
    p.add_argument("--public", action="store_true",
                   help="anyone on the internet can open it; only for mockups with nothing private")

    p = actions.add_parser("update", help="republish a mockup; same name, same URL")
    _common(p)
    p.add_argument("name")
    p.add_argument("directory")
    p.add_argument("--title")

    p = actions.add_parser("list", help="list mockups")
    _common(p)
    for name, text in (("get", "show one mockup"), ("delete", "delete one mockup and its assets")):
        p = actions.add_parser(name, help=text)
        _common(p)
        p.add_argument("name")

    p = actions.add_parser("pull", help="rebuild a mockup folder")
    _common(p)
    p.add_argument("name")
    p.add_argument("directory")
    p.add_argument("--force", action="store_true", help="write into a non-empty folder")

    p = actions.add_parser("share", help="make or revoke expiring public share links")
    _common(p)
    p.add_argument("name")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--ttl", help="lifetime, e.g. 24h or 7d")
    group.add_argument("--revoke", action="store_true", help="revoke every active link")


# -- plumbing ----------------------------------------------------------------

def _client(cfg, args):
    from sandbox.notes.service import NotesClient
    from sandbox.notes.transport import BrokerTransport

    service = notes_config.resolve(cfg, getattr(args, "remote", None))
    transport = (_transport_factory(service) if _transport_factory is not None
                 else BrokerTransport(service))
    return service, NotesClient(transport, service.remote)


def _refuse_in_job(what: str) -> None:
    if os.environ.get("SANDBOX_DURABLE_JOB_ID"):
        raise NotesError("notes_refused_in_job",
                         f"{what} is refused inside a durable job: job output is retained, "
                         "and this would put a secret in it. Run it in a terminal.")


def _emit(args, payload: dict, lines: list[str]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
    else:
        for line in lines:
            print(line)


def _fail(args, exc: NotesError) -> None:
    if getattr(args, "json", False):
        print(json.dumps(exc.to_payload(), sort_keys=True))
    else:
        print(f"error: {exc.message} [{exc.code}]", file=sys.stderr)
    raise SystemExit(1)


def _ensure_personal_file() -> None:
    """The broker updates an existing personal file only; create an empty owner-only one."""
    from sandbox.core._secrets import secret_file

    path = secret_file()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return
    except OSError as exc:
        raise NotesError("notes_store_failed",
                         f"cannot create the personal secrets file: {exc.strerror}") from None
    with os.fdopen(fd, "w") as handle:
        handle.write("# Personal secrets. Keep this file out of Git.\n")


def _store(service):
    from sandbox.secrets import SecretBrokerError
    from sandbox.notes.transport import default_service_factory

    def store(value: str) -> None:
        if service.token_source == "personal":
            _ensure_personal_file()
        try:
            default_service_factory(service.token_source)({}).set(
                service.token_source, service.token_key, value, intent="either",
                input_channel="minted", surface="cli")
        except SecretBrokerError as exc:
            raise NotesError(
                "notes_store_failed",
                f"the token was minted but the broker did not store it ({exc.code}); "
                "rerun `sb notes login` (it revokes the unstored token)") from None
    return store


# -- notes -------------------------------------------------------------------

def _notes(cfg, args) -> None:
    action = args.notes_action
    if action == "config":
        if args.url:
            if not args.remote:
                raise NotesError("notes_input_invalid", "--url needs --remote NAME")
            written = notes_config.write_service(args.remote, args.url,
                                                 make_default=True if args.default else None)
            _emit(args, {"ok": True, **written},
                  [f"{written['remote']}: {written['url']}"
                   + (" (default)" if written["default_remote"] == written["remote"] else "")])
            return
        if args.default:
            raise NotesError("notes_input_invalid", "--default needs --remote and --url")
        service = notes_config.resolve(cfg, args.remote)
        payload = {"ok": True, "remote": service.remote, "url": service.url,
                   "token": {"source": service.token_source, "key": service.token_key}}
        _emit(args, payload, [f"{service.remote}: {service.url} "
                              f"(token {service.token_source}:{service.token_key})"])
        return
    if action == "login":
        from sandbox.notes.login import default_token_name, login
        _refuse_in_job("sb notes login")
        if args.url:
            remote = args.remote or (cfg.get("notes") or {}).get("default_remote")
            if not remote:
                raise NotesError("notes_input_invalid", "--url needs --remote NAME")
            notes_config.write_service(remote, args.url)
            from sandbox.core._config import load_config
            cfg = load_config()
            args.remote = remote
        service, client = _client(cfg, args)
        verify = None if args.no_verify else (lambda: client.one("list_notes", limit=1))
        result = login(service, token_name=args.token_name or default_token_name(),
                       store=_store(service), verify=verify)
        _emit(args, {"ok": True, **result}, [
            f"stored a new token '{result['token_name']}' for {service.remote} "
            f"in {service.token_source}:{service.token_key}"
            + (f"; revoked {result['revoked_previous']} older" if result["revoked_previous"] else "")
            + ("; verified" if result["verified"] else "")])
        return

    service, client = _client(cfg, args)
    if action == "create":
        note = client.create(Path(args.file), title=args.title, html=args.html, public=args.public)
        _emit(args, {"ok": True, "note": note}, [note.get("slug", ""), note.get("url", "")])
    elif action == "update":
        note = client.update(args.slug, Path(args.file), title=args.title)
        _emit(args, {"ok": True, "note": note}, [note.get("slug", ""), note.get("url", "")])
    elif action == "get":
        note = client.get(args.slug)
        _emit(args, {"ok": True, "note": note}, [note.get("content") or ""])
    elif action == "list":
        notes = client.list()
        _emit(args, {"ok": True, "count": len(notes), "notes": notes},
              [f"{n.get('slug')}  {n.get('updated_at')}  {n.get('title')}  {n.get('url')}"
               for n in notes] or ["(no notes)"])
    elif action == "delete":
        client.delete(args.slug)
        _emit(args, {"ok": True, "slug": args.slug, "deleted": True}, [f"deleted {args.slug}"])
    elif action == "upload":
        asset = client.upload(args.slug, Path(args.file))
        _emit(args, {"ok": True, "asset": asset}, [asset.get("path", ""), asset.get("url", "")])
    elif action == "share":
        if args.revoke:
            result = client.revoke_shares(args.slug)
            _emit(args, {"ok": True, **result}, [f"revoked {len(result['revoked'])} link(s)"])
        else:
            from sandbox.notes.service import ttl_hours
            hours = ttl_hours(args.ttl)
            _refuse_in_job("creating a share link")
            link = client.share(args.slug, hours)
            link.pop("message", None)
            _emit(args, {"ok": True, "link": link},
                  [link.get("url", ""), f"expires {link.get('expires_at')}  id {link.get('id')}"])
    elif action == "sync":
        report = client.sync(Path(args.directory), prune=args.prune, adopt=args.adopt,
                             create_duplicates=args.create_duplicates)
        lines = []
        for bucket in ("created", "adopted", "updated", "recreated", "unchanged", "pruned"):
            for item in report[bucket]:
                lines.append(f"{bucket:9}  {item['file']}  {item.get('url') or item.get('slug')}")
        for item in report["orphaned"]:
            lines.append(f"orphaned   {item['file']}  {item['slug']}  (file gone; --prune deletes it)")
        for item in report["failed"]:
            lines.append(f"failed     {item['file']}  {item['error']} [{item['code']}]")
        _emit(args, report, lines or ["(no .md files)"])
        if not report["ok"]:
            raise SystemExit(1)


# -- mockups -----------------------------------------------------------------

def _mockups(cfg, args) -> None:
    from sandbox.notes.mockups import Mockups

    _, client = _client(cfg, args)
    mockups = Mockups(client)
    action = args.mockups_action
    if action == "publish":
        note = mockups.publish(Path(args.directory), args.name, title=args.title, public=args.public)
        _emit(args, {"ok": True, "mockup": note}, [note.get("url", "")])
    elif action == "update":
        note = mockups.update(args.name, Path(args.directory), title=args.title)
        _emit(args, {"ok": True, "mockup": note}, [note.get("url", "")])
    elif action == "list":
        items = mockups.list()
        _emit(args, {"ok": True, "count": len(items), "mockups": items},
              [f"{m['name']}  {m.get('updated_at')}  {m.get('url')}" for m in items]
              or ["(no mockups)"])
    elif action == "get":
        data = mockups.get(args.name)
        assets = data.get("assets") or []
        if not getattr(args, "json", False):
            data.pop("content", None)
        _emit(args, {"ok": True, "mockup": data},
              [f"{data['name']}  {data.get('url')}", f"slug {data.get('slug')}  "
               f"updated {data.get('updated_at')}  {len(assets)} asset(s)"])
    elif action == "pull":
        result = mockups.pull(args.name, Path(args.directory), force=args.force)
        _emit(args, {"ok": True, **result},
              [f"wrote {len(result['files'])} file(s) to {result['directory']}"])
    elif action == "delete":
        result = mockups.delete(args.name)
        _emit(args, {"ok": True, **result}, [f"deleted {args.name}"])
    elif action == "share":
        if args.revoke:
            result = mockups.revoke(args.name)
            _emit(args, {"ok": True, **result}, [f"revoked {len(result['revoked'])} link(s)"])
        else:
            from sandbox.notes.service import ttl_hours
            hours = ttl_hours(args.ttl)
            _refuse_in_job("creating a share link")
            link = mockups.share(args.name, hours)
            link.pop("message", None)
            _emit(args, {"ok": True, "link": link},
                  [link.get("url", ""), f"expires {link.get('expires_at')}  id {link.get('id')}"])


def cmd_notes(cfg, args) -> None:
    """Publish and manage Agent Notes notes; the token stays in the secrets broker."""
    try:
        _notes(cfg, args)
    except NotesError as exc:
        _fail(args, exc)
    except (OSError, UnicodeError) as exc:
        _fail(args, NotesError("notes_io_error", f"{type(exc).__name__}: {exc}"))


def cmd_mockups(cfg, args) -> None:
    """Publish a folder with index.html as an Agent Notes HTML mockup."""
    try:
        _mockups(cfg, args)
    except NotesError as exc:
        _fail(args, exc)
    except (OSError, UnicodeError) as exc:
        _fail(args, NotesError("notes_io_error", f"{type(exc).__name__}: {exc}"))


register_specs((
    CommandSpec("notes", cmd_notes, configure=configure_notes, owner=__name__, scope="global",
                predispatch_policy=lambda _args: True,
                help="publish notes to the Agent Notes service (token held by the secrets broker)"),
    CommandSpec("mockups", cmd_mockups, configure=configure_mockups, owner=__name__,
                scope="global", predispatch_policy=lambda _args: True,
                help="publish a folder with index.html as an Agent Notes HTML mockup"),
))


__all__ = ["cmd_notes", "cmd_mockups", "configure_notes", "configure_mockups"]
