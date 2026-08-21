from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr



from sandbox.core import (
    CONFIG_LOCAL, ROOT, RUNTIME_DIR, _cert_paths, _cleanup_herd_route, _core, _herd,
    _herd_tests_db, _hosts_edit, _local_yaml, _provision_test_harness,
    _valet_proxy_active, _write_local_yaml, active_project_file,
    collect_instance_rows, compose, compose_file, die, ensure_instance, ensure_pyyaml,
    focus_file, info, load_config, ok, proxy_available, regen_caddyfile,
    reload_proxy, resolve_instances, snapshots_dir, valet_proxy_remove, wp_dir,
    wpcli, _write_abilities_context,
)

from sandbox.registry import CommandSpec, register, register_specs
from sandbox.application.context import (
    domain_service, ingress_service, runtime_service, wordpress_runtime_service,
)
from sandbox.runtimes.base import OperationError, OperationRequest
from sandbox.services.redaction import REDACTION_FAILED, redact_structure, redact_text


_GENERIC_INIT_TYPES = frozenset({
    "compose", "generic", "astro", "laravel", "php", "node", "javascript",
})

_GENERIC_INIT_CHOICES = (
    "compose", "generic", "astro", "laravel", "php", "node", "javascript",
)


def _is_explicit_generic_init(args) -> bool:
    """Return whether parsed args select the initialization-only generic path.

    This predicate belongs to the instance command because it describes the
    command's own parser contract. The CLI only asks the resolved
    :class:`~sandbox.registry.CommandSpec` for its predispatch policy, keeping
    generic type knowledge out of the compatibility composition root.
    """
    if getattr(args, "cmd", None) != "init":
        return False
    value = getattr(args, "type", None)
    return isinstance(value, str) and value.strip().lower() in _GENERIC_INIT_TYPES


def configure_init_parser(parser) -> None:
    """Compose the feature-owned ``sb init`` parser beside its handler."""
    parser.add_argument(
        "--project-dir", dest="project_dir", default=None,
        help="project directory (default: current directory)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="regenerate sandbox.config.json even if one already exists",
    )
    parser.add_argument(
        "--no-test-harness", dest="no_test_harness", action="store_true",
        help="skip provisioning the phpunit test harness",
    )
    parser.add_argument(
        "--type", choices=_GENERIC_INIT_CHOICES,
        help=("explicit generic project type; validate/write config only, "
              "then run sb ensure"),
    )


def generic_init_predispatch_policy(args) -> bool:
    """Tell the CLI to skip shared writes for explicit generic initialization."""
    return _is_explicit_generic_init(args)


def _generic_init_family(value: object) -> str:
    """Return the comparison family for an explicit generic init type.

    The CLI deliberately keeps the caller's raw ``--type`` value for the
    reviewable descriptor.  This helper is only for conflict checks: Node and
    JavaScript are equivalent aliases, while Compose/generic are the broad
    framework-neutral family.
    """
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    if value in {"node", "javascript", "js"}:
        return "node"
    if value in {"compose", "generic"}:
        return "compose"
    if value == "laravel-sail":
        return "laravel"
    return value


def _generic_descriptor_document(project_root: Path) -> dict:
    """Read the selected native descriptor for an init conflict check.

    ``load_project_config`` intentionally normalizes kind aliases to
    ``compose``.  Init needs the raw declared kind too, so a request such as
    ``--type laravel`` cannot silently reinterpret an existing explicit
    ``kind: astro``/``kind: wordpress`` document.  This is read-only and is
    kept local to the CLI compatibility surface.
    """
    try:
        from sandbox.config.descriptors import _load_mapping, primary_config
        path = primary_config(project_root)
        return _load_mapping(path) if path is not None else {}
    except (OSError, ValueError, TypeError):
        # The canonical loader below remains authoritative for malformed or
        # ambiguous descriptors; this helper must not hide that error.
        return {}


def _generic_init_conflict(requested_type: str, descriptor: Mapping,
                           resolved: Mapping) -> str | None:
    """Return a deterministic conflict message, or ``None`` when compatible."""
    requested_family = _generic_init_family(requested_type)
    raw_kind = descriptor.get("kind")
    raw_framework = descriptor.get("framework") or descriptor.get("preset")

    # A WordPress descriptor is never converted implicitly by a generic init
    # request.  This check happens before any runtime call (and before force
    # regeneration) so an accidental type cannot boot or rewrite a plugin.
    resolved_kind = str(resolved.get("kind") or "").strip().lower()
    if resolved_kind != "compose":
        return (f"--type {requested_type!r} conflicts with the existing "
                f"project kind {resolved_kind or 'unknown'!r}; keep the "
                "WordPress descriptor or remove it before generic init")

    # An explicitly declared framework/kind is a stronger signal than the
    # normalized compose kind.  Broad compose/generic declarations can be
    # refined by a specific preset; specific declarations may not be changed
    # in place by a differently typed init request.
    declared = raw_framework if isinstance(raw_framework, str) else raw_kind
    declared_family = _generic_init_family(declared)
    if declared_family and declared_family != "compose":
        if requested_family != declared_family:
            return (f"--type {requested_type!r} conflicts with the existing "
                    f"project type {declared!r}")

    resolved_framework = resolved.get("framework")
    if isinstance(resolved_framework, str) and resolved_framework.strip():
        resolved_family = _generic_init_family(resolved_framework)
        if (resolved_family and resolved_family != "compose"
                and requested_family != resolved_family):
            return (f"--type {requested_type!r} conflicts with the existing "
                    f"project framework {resolved_framework!r}")
    return None


def _finish_generic_init(project_root: Path, requested_type: str,
                         resolved: Mapping) -> None:
    """Report reviewable generic config without starting project processes."""
    framework = resolved.get("framework") or requested_type
    ok(f"validated generic {framework} project configuration")
    print(f"  project: {project_root}")
    print(f"  next: ./sb ensure --project-dir {project_root}")


def _is_wordpress_project(config: dict) -> bool:
    """Return whether a resolved descriptor uses the WordPress schema."""
    return config.get("kind") == "wordpress"


_REDACTION_MARKERS = frozenset({"[REDACTED]", "[REDACTION_FAILED]"})
_RAW_SANDBOX_AUTOLOGIN_QUERY = re.compile(
    r"(?i)(?:[?&;])sandbox_autologin(?:=|[&;#]|$)"
)


def _sandbox_autologin_value(value: object) -> str | None:
    """Return the safe-to-classify ``sandbox_autologin`` query value.

    This helper is deliberately only a classifier.  It never returns a value
    to the caller's output path; the sole caller that needs the raw URL still
    validates the URL and the explicit reveal policy separately.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        for name, item in parse_qsl(
                parsed.query, keep_blank_values=True, strict_parsing=False):
            if name.lower() == "sandbox_autologin":
                return item
    except (TypeError, ValueError):
        return None
    return None


def _document_has_sandbox_autologin(document: object) -> bool:
    """Classify raw input as a boolean without returning any of it.

    ``redact_structure`` deliberately turns malformed credential-bearing URLs
    into ``[REDACTION_FAILED]``.  Once that happens the sanitized document no
    longer retains the query name, so record this boolean before redaction.
    It is used only to decide whether to emit the derived status field; a
    producer-supplied status is never an input to this classification.
    """
    if not isinstance(document, Mapping):
        return False
    value = document.get("login_url")
    if not isinstance(value, str):
        return False
    return (_sandbox_autologin_value(value) is not None
            or bool(_RAW_SANDBOX_AUTOLOGIN_QUERY.search(value)))


def _print_ensure_json(document: object, *, sort_keys: bool = False,
                       compact: bool = False, reveal_login: bool = False) -> None:
    """Emit one fail-closed public JSON document for ``sb ensure --json``.

    ``reveal_login`` is the ``--reveal-login`` opt-in and restores the single
    ``login_url`` field after redaction. Default output stays redacted, so the
    guarantee is unchanged for every caller that does not ask. Without the
    opt-in the redacted value still carries the ``sandbox_autologin=``
    parameter name, so consumers cannot distinguish it from a working token by
    shape alone.

    Two records qualify. A LOCAL instance whose URL is loopback-bound: that
    token is a dev credential the caller already owns (stored in
    ``sandbox.local.yml`` under the same UID) and authenticates nothing off
    this machine. And a REMOTE ensure record, which an E2E runner needs for the
    same reason a local one does -- note that a remote instance exposed on a
    public hostname makes the revealed URL an admin credential for anyone who
    obtains it, so it belongs in a gitignored descriptor, never in logs or a
    commit.
    """
    # Preserve this classification before redaction so malformed token-bearing
    # URLs that become ``[REDACTION_FAILED]`` still advertise that the emitted
    # login URL is redacted.  It is a local boolean only; the classification
    # never reaches the output document.  A validated raw URL is emitted only
    # by the explicit reveal path below.
    has_autologin = _document_has_sandbox_autologin(document)
    payload = redact_structure(document)
    if has_autologin and payload == REDACTION_FAILED:
        # A malformed URL can make the recursive redactor fail at the document
        # boundary.  Retain only the marker, not any sibling input fields, so
        # the public JSON can still report the derived redaction state.
        payload = {"login_url": REDACTION_FAILED}
    if isinstance(payload, dict):
        # Discard a producer-supplied status.  ``has_autologin`` is the
        # boolean-only raw-input classification from above; neither the raw
        # URL nor token is copied into this public document.
        payload.pop("login_url_redacted", None)
        revealed = ""
        if (reveal_login and isinstance(document, Mapping)):
            revealed = _autologin_url_to_reveal(document)
            if revealed:
                payload["login_url"] = revealed
        if has_autologin:
            # ``false`` is reserved for the one path above: a successful,
            # explicitly requested reveal.  A placeholder, unusable URL,
            # non-loopback local target, or already-redacted input is true.
            payload["login_url_redacted"] = not bool(revealed)
    separators = (",", ":") if compact else None
    print(json.dumps(
        payload,
        sort_keys=sort_keys,
        separators=separators,
        default=str,
    ))


def _autologin_url_to_reveal(document: Mapping) -> str:
    """Return the autologin URL the ``--reveal-login`` opt-in may emit.

    A remote ensure record qualifies on the strength of the flag alone: the
    caller selected that remote, and the runner it feeds needs a usable login.
    A local record still has to prove its host is loopback-bound, so a
    deployed or rewritten local URL never leaks a token by accident.
    Empty string when the record carries no autologin token at all.
    """
    value = document.get("login_url")
    token = _sandbox_autologin_value(value)
    if (not isinstance(value, str) or token is None or not token
            or token in _REDACTION_MARKERS):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    target = document.get("target")
    if isinstance(target, Mapping) and target.get("remote"):
        return value
    host = urlsplit(value).hostname or ""
    if host in {"localhost", "::1"} or host.startswith("127."):
        return value
    try:
        resolved = socket.gethostbyname(host)
    except (OSError, ValueError):
        return ""
    return value if resolved.startswith("127.") else ""



def cmd_focus(cfg, args) -> None:
    inst = args.resolved_instance
    ff = focus_file(inst)
    if args.clear:
        if ff.exists():
            ff.unlink()
        _write_abilities_context(inst)
        ok("Cleared plugin focus")
        return
    if not args.slug:
        if ff.exists():
            print(ff.read_text().strip())
        else:
            info("No focused plugin set")
        return
    # Singleton focus invariant: a plugin is focused in AT MOST one
    # instance at a time. Before focusing it here, clear the same plugin's
    # focus on every OTHER instance. This is what lets "focus <plugin>"
    # resolve to exactly one instance later — the plugin name, not the
    # instance name, becomes the unambiguous lookup key.
    #   `--here` is the explicit override: focus here WITHOUT stealing it
    #   from other instances (for deliberate A/B / multi-instance work).
    if not getattr(args, "here", False):
        stolen = []
        for fp in ROOT.glob(".focus.*"):
            other = fp.name[len(".focus."):]
            if other == inst:
                continue
            try:
                matches = fp.read_text().strip() == args.slug
            except OSError:
                continue
            if matches:
                fp.unlink()
                _write_abilities_context(other)
                stolen.append(other)
        if stolen:
            info(f"moved focus of '{args.slug}' here from: "
                 f"{', '.join(stolen)}")

    ff.write_text(args.slug)
    _write_abilities_context(inst)
    ok(f"Focused plugin: {args.slug}")

def cmd_ensure(cfg, args) -> None:
    """`./sb ensure [--project-dir DIR]` — boot the instance for a project
    directory (create-if-missing) and print its URL. The MCP server's
    ensure_instance tool wraps this; also usable by hand."""
    if not getattr(args, "local", False):
        from sandbox.commands.lifecycle import _remote_lifecycle
        remote_result = _remote_lifecycle(cfg, args, "ensure")
        if remote_result is not None and getattr(args, "json", False):
            _print_ensure_json(remote_result, sort_keys=True,
                               reveal_login=getattr(args, "reveal_login", False))
        elif remote_result is not None:
            print(f"remote workspace {getattr(args, 'workspace', None) or getattr(args, 'label', 'default')}: ready")
        if remote_result is not None:
            return
    sc = _core()
    pd = getattr(args, "project_dir", None) or os.getcwd()
    label = getattr(args, "label", None)
    create = getattr(args, "create", False)
    try:
        result = wordpress_runtime_service(cfg).invoke(OperationRequest(
            project_root=pd,
            operation="ensure",
            label=label or "default",
            arguments={"create": create},
        ))
    except sc.ConfigError as e:
        message = str(e)
        if getattr(args, "json", False):
            _print_ensure_json({
                "ok": False,
                "error": {"code": "config_error", "message": message},
            }, compact=True)
            raise SystemExit(1)
        die(redact_text(message))
    if isinstance(result, OperationError):
        if getattr(args, "json", False):
            _print_ensure_json({
                "ok": False,
                "error": {"code": result.code, "message": result.message},
            }, compact=True)
            raise SystemExit(1)
        die(redact_text(result.message))
    entry = dict(result.data)
    if not isinstance(entry, dict) or "instance" not in entry:
        # A runtime that refuses returns its own typed result, not an instance
        # record; crashing on the missing key hid the actual reason from the
        # operator and printed a traceback instead.
        reason = (entry or {}).get("reason") if isinstance(entry, dict) else None
        detail = (entry or {}).get("error") if isinstance(entry, dict) else None
        error = detail if isinstance(detail, dict) else None
        code = (reason.get("code") if isinstance(reason, dict) else reason) \
            or (error.get("code") if error else None)
        # A runtime that succeeds without an instance record is not a failure.
        # Managed-native reports a backend and health instead of the Compose
        # instance entry, and printing "instance is not ready: ready" for a
        # working instance is worse than useless.
        if isinstance(entry, dict) and entry.get("ok"):
            if getattr(args, "json", False):
                _print_ensure_json(
                    entry,
                    compact=True,
                    reveal_login=getattr(args, "reveal_login", False),
                )
            else:
                backend = entry.get("backend") or {}
                where = (f"{backend.get('address')}:{backend.get('port')}"
                         if backend.get("address") else entry.get("state", "ready"))
                ok(f"Instance ready: {where}")
            return
        # A failure is the one result an operator most needs the detail of. The
        # runtime already reports which step it failed after and why; printing
        # the code alone made a 34-minute provisioning failure indistinguishable
        # from any other, so emit the whole payload under --json and the
        # message plus the completed steps otherwise.
        if getattr(args, "json", False):
            _print_ensure_json(entry, compact=True)
        message = (reason.get("message") if isinstance(reason, dict) else None) \
            or (error.get("message") if error else None)
        failed_after = reason.get("failed_after") if isinstance(reason, dict) else None
        summary = f"instance is not ready: {code or detail or 'no reason reported'}"
        if message and message != code:
            summary += f": {message}"
        if failed_after:
            summary += f" (completed: {', '.join(str(step) for step in failed_after)})"
        die(redact_text(summary))
    if getattr(args, "json", False):
        # Compact single line as the LAST stdout line so the MCP server can
        # parse it past any boot/progress output above. The record here is
        # always a local instance -- the remote branch returned long before,
        # honouring --reveal-login on its own record.
        _print_ensure_json(entry, reveal_login=getattr(args, "reveal_login", False))
    else:
        ok(f"instance '{entry['instance']}' ready at {entry['url']}")
        print(f"  project: {entry['root']}")
        if entry.get("kind") == "compose":
            print(f"  kind:    compose  service={entry.get('service')} http={entry.get('http_port')}")
        else:
            print(f"  ports:   WP={entry['wordpress_port']} "
                  f"DB={entry['db_port']} mail={entry['mailpit_port']}")
            print(f"  server:  {entry['server']}  (source: {entry.get('source')})")

def cmd_init(cfg, args) -> None:
    """Initialize a project descriptor, preserving the legacy WordPress boot.

    An explicit generic ``--type`` is intentionally initialization-only: it
    may inspect or write a reviewable Compose descriptor, but project code is
    not started until the operator runs ``sb ensure``.  Omitting ``--type``
    retains the historical WordPress (and already-configured Compose) boot
    behavior for compatibility.
    """
    pd = getattr(args, "project_dir", None) or os.getcwd()
    sc = _core()
    requested_type = getattr(args, "type", None)
    project_root = Path(pd).expanduser().resolve()

    # Keep the raw spelling for the generated, reviewable framework field;
    # parser choices already constrain normal CLI callers, while this guard
    # keeps direct command users from bypassing the same contract.
    if requested_type:
        requested_type = str(requested_type).strip().lower()
        if requested_type not in _GENERIC_INIT_TYPES:
            die(f"unsupported generic project type: {requested_type!r}")

        # ``primary_config`` also sees the conventional nested config home;
        # the root-level fallback keeps small test doubles and old callers
        # compatible with the command's prior ``CONFIG_BASENAMES`` contract.
        try:
            from sandbox.config.descriptors import primary_config
            native_path = primary_config(project_root)
        except (ImportError, OSError, TypeError, ValueError):
            native_path = next(
                (project_root / name for name in sc.CONFIG_BASENAMES
                 if (project_root / name).exists()), None,
            )

        if native_path is None:
            # Astro's preset is read-only with respect to project execution:
            # it examines package metadata and writes only explicit files.
            if requested_type == "astro":
                from sandbox.runtimes.presets import propose_astro
                propose_astro(project_root)
                ok("wrote reviewable Astro Compose and Sandbox configuration")
            else:
                compose_file = next(
                    (project_root / name for name in (
                        "compose.yaml", "compose.yml", "docker-compose.yml",
                    ) if (project_root / name).exists()), None,
                )
                if compose_file is None:
                    die("generic initialization requires an existing compose.yaml, "
                        "compose.yml, or docker-compose.yml; no project command "
                        "was guessed")
                ensure_pyyaml()
                import yaml
                document = yaml.safe_load(compose_file.read_text()) or {}
                services = document.get("services") if isinstance(document, dict) else None
                if not isinstance(services, dict) or not services:
                    die("generic initialization found no Compose services")
                preferred = ("web", "app", "laravel.test", "node", "frontend")
                service = next((name for name in preferred if name in services), next(iter(services)))
                service_doc = services.get(service) or {}
                ports = service_doc.get("expose") or service_doc.get("ports") or []
                internal_port = 80
                if ports:
                    raw = ports[0]
                    raw = (raw.get("target") if isinstance(raw, dict)
                           else str(raw).split(":")[-1].split("/")[0])
                    try:
                        internal_port = int(raw)
                    except (TypeError, ValueError):
                        pass
                config = {
                    "kind": "compose", "framework": requested_type,
                    "compose": {"file": compose_file.name, "service": service,
                                 "internal_port": internal_port, "health_path": "/"},
                }
                (project_root / "sandbox.config.json").write_text(
                    json.dumps(config, indent=2) + "\n",
                )
                ok(f"wrote sandbox.config.json for generic {requested_type} project")

        # Resolve/validate the descriptor after any proposal has been written,
        # but before deciding what (if anything) to boot.  The raw document is
        # retained for explicit kind/framework conflict checks because the
        # schema facade normalizes all generic aliases to ``kind=compose``.
        descriptor = _generic_descriptor_document(project_root)
        try:
            pconf = sc.load_project_config(pd)
        except sc.ConfigError as e:
            die(str(e))
        conflict = _generic_init_conflict(requested_type, descriptor, pconf)
        if conflict:
            die(conflict)
        if pconf.get("kind") == "compose":
            _finish_generic_init(project_root, requested_type, pconf)
            return

    # Legacy/no-type flow below intentionally retains its historical boot and
    # test-harness provisioning behavior.
    try:
        pconf = sc.load_project_config(pd)
    except sc.ConfigError as e:
        die(str(e))
    root = Path(pconf["root"])
    base_source = pconf["source"].split("+")[0]   # drop any "+override" suffix
    has_native = base_source in sc.CONFIG_BASENAMES
    force = getattr(args, "force", False)

    if pconf.get("kind") == "compose":
        result = runtime_service(cfg).invoke(OperationRequest(
            project_root=str(root), operation="ensure", label="default",
            arguments={"create": True},
        ))
        if isinstance(result, OperationError):
            die(result.message)
        entry = dict(result.data)
        ok(f"generic instance '{entry['instance']}' ready at {entry['url']}")
        print(f"  project: {entry['root']}\n  service: {entry['service']}\n  framework: {entry.get('framework') or 'compose'}")
        return

    # 1. Ensure a native config exists (don't clobber unless --force).
    if has_native and not force:
        info(f"config exists: {base_source} (use --force to regenerate)")
    else:
        # The resolved pconf already carries the canonical schema (defaults
        # merged, .wp-env.json mapped). Persist exactly the schema keys.
        data = {k: pconf.get(k, v) for k, v in sc.DEFAULTS.items()}
        # New WordPress scaffolds opt into the reviewed profile explicitly.
        # Keep existing descriptors (including --force regeneration) unchanged
        # when they omitted the field; generic Compose returns above.
        if _is_wordpress_project(pconf) and not has_native:
            data["phpExtensions"] = {"profile": "wordpress@1"}
        if base_source == "defaults":
            # Defaults use the canonical map so Query Monitor can be declared
            # installed-but-inactive. Add this checkout under its real slug at
            # scaffold time; a map key, unlike legacy ["."], is worktree-safe.
            project_slug = str(data.get("slug") or root.name).strip()
            data["plugins"] = {
                project_slug: ".",
                **dict(data.get("plugins") or {}),
            }
        # Regenerate the SAME native file (preserving an existing .yml/.yaml);
        # scaffold/convert to sandbox.config.json. Writing a fresh .json beside
        # an existing .yml would shadow it (json wins load order) and silently
        # orphan the user's edit surface.
        dest = root / (base_source if has_native else "sandbox.config.json")
        if dest.suffix in (".yml", ".yaml"):
            ensure_pyyaml()
            import yaml
            dest.write_text(yaml.safe_dump(data, default_flow_style=False,
                                           sort_keys=False))
        else:
            dest.write_text(json.dumps(data, indent=2) + "\n")
        note = ("converted from .wp-env.json" if base_source == ".wp-env.json"
                else "regenerated" if has_native else "scaffolded")
        ok(f"wrote {dest.name} ({note})")

    # 2. Boot the instance for this project (idempotent).
    try:
        entry = ensure_instance(cfg, str(root))
    except sc.ConfigError as e:
        die(str(e))
    inst = entry["instance"]
    ok(f"instance '{inst}' ready at {entry['url']}")

    # 3. Provision the test harness so `./sb test` runs immediately.
    if not getattr(args, "no_test_harness", False):
        info("Provisioning test harness (cached)…")
        _provision_test_harness(inst, sc.load_project_config(str(root)))
        ok("test harness ready")

    print()
    ok(f"{root.name} is a sandbox project.")
    print(f"  admin:  {entry['url']}/wp-admin")
    print(f"  test:   ./sb test --project-dir {root}")

def _cleanup_instance_routes(cfg, owner) -> None:
    """Persist route cleanup outcomes while project identity is still available."""
    if not owner or not owner.get("root"):
        return
    ingress_result = ingress_service(cfg).cleanup_owner(
        f"{Path(owner['root']).expanduser().resolve()}::{owner.get('label', 'default')}",
    )
    if ingress_result["state"] == "cleanup_incomplete":
        info("ingress cleanup is incomplete; recovery state was retained for retry")
    elif ingress_result["ok"] and ingress_result["mutated"]:
        info("removed unchanged owned incumbent ingress routes")
    try:
        domain_result = domain_service(cfg).cleanup(
            owner["root"], label=owner.get("label", "default"), interactive=False,
        )
    except Exception as exc:
        if not isinstance(exc, (OSError, ValueError)) and exc.__class__.__name__ != "ConfigError":
            raise
        info(f"resolver cleanup could not inspect the project; retained state for retry: {exc}")
        return
    if domain_result.state == "cleanup_incomplete":
        info("resolver cleanup is incomplete; recovery state was retained for retry")
    elif domain_result.ok and domain_result.mutated:
        info("removed owned scoped resolver bindings")


def _cleanup_native_owner(cfg, owner) -> bool:
    """Retain registry identity when conservative native cleanup is incomplete.

    Ingress cleanup remains an independent A-owned transaction.  It is invoked
    while the owner identity exists, but no local registry identity is removed
    until C reports that all of its owned resources were safely reconciled.
    """
    _cleanup_instance_routes(cfg, owner)
    result = runtime_service(cfg).invoke(OperationRequest(
        owner["root"], "destroy", label=owner.get("label", "default"),
    ))
    if isinstance(result, OperationError):
        info(f"native cleanup is blocked; retained identity for retry: {result.code}")
        return False
    data = dict(result.data)
    cleanup = data.get("cleanup") if isinstance(data.get("cleanup"), dict) else {}
    if not result.ok or cleanup.get("complete") is not True:
        info("native cleanup is incomplete; retained identity and recovery state for retry")
        return False
    return True


def cmd_instance(cfg, args) -> None:
    """Delete a sandbox instance end-to-end.

    `./sb instance delete <name>` stops the stack, removes the volume, deletes
    runtime/wp-<name>/, removes the block from sandbox.local.yml, drops the
    registry mapping, and removes per-instance state. Refuses to delete `main`.

    Per-project model: instances are CREATED by `./sb init` / `./sb ensure` in a
    plugin dir (keyed to the project root) — not by name here.
    """
    action = args.action
    name = args.name

    if action != "delete":
        die("`./sb instance` only supports `delete` now. Create an instance by "
            "cd-ing into a plugin repo and running `./sb init` (or `./sb ensure`).")

    owner = _core().registry_find_instance(name)
    if owner and owner.get("runtime_mode") == "managed_native":
        if not _cleanup_native_owner(cfg, owner):
            return
        _core().registry_remove(owner["root"], label=owner.get("label"))
        info(f"deregistered '{name}' after complete native cleanup")
        ok(f"Native instance '{name}' deleted.")
        return

    if owner and owner.get("kind") == "compose":
        _cleanup_instance_routes(cfg, owner)
        result = runtime_service(cfg).invoke(OperationRequest(
            owner["root"], "destroy", label=owner.get("label", "default"),
        ))
        if isinstance(result, OperationError):
            die(result.message)
        ok(f"Generic instance '{name}' deleted without removing project volumes.")
        return

    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,30}$", name or ""):
        die("instance name must be lowercase, start with [a-z0-9], "
            "and contain only [a-z0-9_-] (max 31 chars)")

    # delete
    instances = resolve_instances(cfg)
    if name not in instances:
        die(f"unknown instance '{name}'. Defined: "
            f"{', '.join(instances.keys())}")

    if not args.yes:
        print(f"\n  This will:")
        print(f"    • stop + remove all containers for instance '{name}'")
        print(f"    • delete the DB volume")
        print(f"    • delete runtime/wp-{name}/ and runtime/snapshots/{name}/")
        print(f"    • remove instances.{name} from sandbox.local.yml")
        print(f"    • delete .focus.{name} / .active-project.{name}")
        print()
        ans = input(f"  Type the instance name '{name}' to confirm: ").strip()
        if ans != name:
            info("cancelled")
            return

    # Resolver and ingress routes are keyed by registered project identity. Reconcile them
    # before stopping the runtime or deleting local/registry identity; an
    # incomplete result is durably retained by DomainRepository and can be
    # retried independently after the instance itself is gone.
    owner = _core().registry_find_instance(name)
    _cleanup_instance_routes(cfg, owner)

    # 1. Stop + remove the runtime. Docker: containers + volume. Herd: drop
    #    the host DBs (while wp-config still exists), then unsecure + unlink.
    args.resolved_instance = name
    if instances[name].get("server") == "herd":
        info(f"herd teardown for '{name}': dropping DBs + unlink + unsecure")
        wpcli(["db", "query",
               f"DROP DATABASE IF EXISTS {_herd_tests_db(name)}"],
              instance=name, check=False, capture=True)
        wpcli(["db", "drop", "--yes"], instance=name, check=False, capture=True)
        # Drop the PHP isolation entry so a stale row doesn't linger in
        # `herd isolated` after the site is gone.
        _herd("unisolate", "--site", name)
        _cleanup_herd_route(name, wp_dir(name) if wp_dir(name).exists() else None)
    else:
        info(f"stopping + removing containers + volume for '{name}'")
        compose("down", "-v", instance=name, check=False)

    # 2. Remove WP install dir + snapshots (+ MCP pinned-PHP shims, if any)
    for path in (wp_dir(name), snapshots_dir(name),
                 RUNTIME_DIR / "herd-shims" / name):
        if path.exists():
            info(f"removing {path}")
            shutil.rmtree(path, ignore_errors=True)

    # 3. Remove state files
    for path in (focus_file(name), active_project_file(name)):
        if path.exists():
            info(f"removing {path}")
            path.unlink()

    # 4. Remove compose file
    cf = compose_file(name)
    if cf.exists():
        cf.unlink()

    # 5. Remove block from sandbox.local.yml (only — never touch sandbox.yml)
    local = _local_yaml()
    if name in (local.get("instances") or {}):
        del local["instances"][name]
        if not local["instances"]:
            del local["instances"]
        _write_local_yaml(local)
        info(f"removed instances.{name} from {CONFIG_LOCAL.name}")

    # 5b. Drop the project→instance mapping from the registry (the source of
    #     truth for per-directory instances), else `ensure_instance` for that
    #     project would later find a stale "ready" record pointing at a
    #     now-deleted stack.
    sc = _core()
    owner = sc.registry_find_instance(name)
    if owner and owner.get("root"):
        sc.registry_remove(owner["root"], label=owner.get("label"))
        info(f"deregistered '{name}' from the instance registry")

    # The receipt-owned ingress/resolver state was reconciled before identity
    # deletion.  Preserve any unreceipted aggregate proxy, Valet, hosts, cert,
    # or DNS artifact; compare-before-remove cannot attribute it safely.
    dom = instances.get(name, {}).get("domain")
    if dom:
        info(f"preserved unreceipted legacy domain artifacts for {dom}")

    ok(f"Instance '{name}' deleted.")

def cmd_instances(cfg, args) -> None:
    """List defined instances + their status and ports. --project-dir filters
    to one project root — useful once a root owns more than one labelled
    instance (multi-instance-per-root)."""
    rows = collect_instance_rows(cfg)
    project_dir = getattr(args, "project_dir", None)
    if project_dir:
        sc = _core()
        try:
            root = str(sc.find_project_root(project_dir))
        except Exception as e:
            die(f"invalid --project-dir {project_dir!r}: {e}")
        owned = {e["instance"] for e in sc.registry_list_for_root(root)}
        rows = [r for r in rows if r["name"] in owned]
    if getattr(args, "json", False):
        # Inventory is observational and may be retained in durable job output.
        # Autologin URLs contain bearer-equivalent query tokens and are not
        # required to identify or operate an instance.
        public_rows = [
            {key: value for key, value in row.items() if key != "login_url"}
            for row in rows
        ]
        print(json.dumps({"ok": True, "instances": public_rows}))
        return
    print()
    print(f"  {'STATUS':<10} {'NAME':<10} {'LABEL':<10} {'URL':<26} {'SERVER':<10} "
          f"{'MCP SERVER':<18} {'PROJECT':<12} FOCUS")
    for r in rows:
        status = "● running" if r["running"] else "○ stopped"
        print(f"  {status:<10} {r['name']:<10} {r['label']:<10} {r['url']:<26} {r['server']:<10} "
              f"{r['mcp_server']:<18} {r['project']:<12} {r['focus']}")
    print()
    print(f"  Claude tools per instance: mcp__<MCP SERVER>__*  "
          f"(e.g. mcp__sandbox__focus_get)")
    print()

register({
    'ensure': cmd_ensure,
    'instances': cmd_instances,
    'instance': cmd_instance,
    'focus': cmd_focus,
})

register_specs((CommandSpec(
    name="init",
    handler=cmd_init,
    owner=__name__,
    order=0,
    configure=configure_init_parser,
    scope="project",
    predispatch_policy=generic_init_predispatch_policy,
    help=("Initialize a project descriptor; explicit generic --type is "
          "review-only (run sb ensure next)"),
),))
