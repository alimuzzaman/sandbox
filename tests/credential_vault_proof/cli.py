"""One fixed offline entrypoint for the Credential Vault proof harness.

The verbs are exactly: validate-manifest, plan, record-acceptance,
record-artifact, finalize, validate-bundle, render-report. There is no verb
that executes a live check, opens a socket, reaches a host, or reads a secret,
and every failure prints a bounded code rather than an exception.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from . import bundle as bundle_module
from . import cleanup as cleanup_module
from . import ledger as ledger_module
from . import manifest as manifest_module
from . import probes as probes_module
from . import report as report_module


VERBS = (
    "validate-manifest", "plan", "record-acceptance", "record-artifact",
    "finalize", "validate-bundle", "render-report",
)
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_BLOCKED = 3
MAX_CLI_OUTPUT_BYTES = 256 * 1024
MAX_ACCEPTANCE_BYTES = 16 * 1024


def _emit(document: Any) -> None:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_CLI_OUTPUT_BYTES:
        payload = '{"code":"output_oversize","location":"harness","ok":false}'
    print(payload)


def _bounded_failure(error: Any) -> dict[str, Any]:
    """Never print an exception's text; only its stable code and location."""
    code = getattr(error, "code", None)
    location = getattr(error, "location", None)
    return {
        "ok": False,
        "code": code if isinstance(code, str) else "harness_failed",
        "location": location if isinstance(location, str) else "harness",
    }


def _load(path: Any) -> dict[str, Any]:
    return manifest_module.load_manifest(path)


class _QuietParser(argparse.ArgumentParser):
    """Refuse without printing argparse's own usage or error prose."""

    def error(self, message: str) -> None:  # pragma: no cover - trivial
        raise SystemExit(2)

    def exit(self, status: int = 0, message: Any = None) -> None:
        raise SystemExit(status)


def configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.description = "Offline Credential Vault proof-run harness (spec 045)"
    parser.add_argument("verb", choices=VERBS)
    parser.add_argument("--manifest")
    parser.add_argument("--ledger")
    parser.add_argument("--request-id")
    parser.add_argument("--bundle")
    parser.add_argument("--artifact")
    parser.add_argument("--artifact-path")
    parser.add_argument("--acceptance")
    parser.add_argument("--at")
    parser.add_argument("--now")
    return parser


def _require(value: Any, code: str) -> Any:
    if value is None:
        raise manifest_module.ManifestError(code, "arguments")
    return value


def run(argv: Any = None) -> int:
    parser = configure_parser(_QuietParser(prog="credential-vault-proof",
                                           add_help=False))
    try:
        args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    except SystemExit:
        _emit({"ok": False, "code": "arguments_invalid", "location": "arguments"})
        return EXIT_REFUSED
    try:
        if args.verb == "validate-manifest":
            document = _load(_require(args.manifest, "manifest_required"))
            _emit({"ok": True, "code": "manifest_verified",
                   "manifest_id": document["manifest_id"],
                   "manifest_digest": manifest_module.manifest_digest(document),
                   "check_count": len(document["checks"]),
                   "artifact_count": len(document["artifacts"])})
            return EXIT_OK
        if args.verb == "plan":
            document = _load(_require(args.manifest, "manifest_required"))
            entries = probes_module.plan(document)
            _emit({"ok": True, "code": "plan_built",
                   "manifest_digest": manifest_module.manifest_digest(document),
                   "entries": [
                        {"check_id": item["check_id"], "kind": item["kind"],
                        "category": item["category"],
                        "expectation": item["expectation"],
                        "argv": list(item["argv"]),
                        "timeout_seconds": item["timeout_seconds"],
                        "max_output_bytes": item["max_output_bytes"]}
                       for item in entries
                   ]})
            return EXIT_OK
        if args.verb == "record-acceptance":
            document = _load(_require(args.manifest, "manifest_required"))
            store = ledger_module.ProofRunLedger(_require(args.ledger, "ledger_required"))
            request_id = _require(args.request_id, "request_id_required")
            store.open_run(request_id=request_id, manifest=document,
                           started_at=_require(args.at, "timestamp_required"))
            raw_acceptance = args.acceptance or "{}"
            if len(raw_acceptance.encode("utf-8")) > MAX_ACCEPTANCE_BYTES:
                raise ledger_module.LedgerError("acceptance_oversize", "acceptance")
            acceptance = json.loads(raw_acceptance)
            record = store.record_acceptance(request_id, acceptance)
            _emit({"ok": record["job"]["state"] == "accepted",
                   "code": f"acceptance_{record['job']['state']}",
                   "request_id": request_id})
            return EXIT_OK if record["job"]["state"] == "accepted" else EXIT_BLOCKED
        if args.verb == "record-artifact":
            document = _load(_require(args.manifest, "manifest_required"))
            store = ledger_module.ProofRunLedger(_require(args.ledger, "ledger_required"))
            path = Path(_require(args.artifact_path, "artifact_path_required"))
            name = _require(args.artifact, "artifact_required")
            expectation = next((item for item in document["artifacts"]
                                if item["name"] == name), None)
            if expectation is None:
                raise bundle_module.BundleError("artifact_unplanned", str(name)[:64])
            raw = bundle_module._read(path, limit=expectation["max_bytes"])
            bundle_module.validate_artifact_payload(name, raw, document)
            digest = hashlib.sha256(raw).hexdigest()
            store.record_artifact(_require(args.request_id, "request_id_required"),
                                  name, digest, manifest=document)
            _emit({"ok": True, "code": "artifact_recorded", "sha256": digest})
            return EXIT_OK
        if args.verb == "finalize":
            document = _load(_require(args.manifest, "manifest_required"))
            store = ledger_module.ProofRunLedger(_require(args.ledger, "ledger_required"))
            record = store.finalize(
                _require(args.request_id, "request_id_required"),
                required=manifest_module.required_check_ids(document),
                terminal_at=_require(args.at, "timestamp_required"),
            )
            _emit({"ok": record["classification"] == "passed_live",
                   "code": record["classification"],
                   "request_id": record["request_id"]})
            return EXIT_OK if record["classification"] == "passed_live" else EXIT_BLOCKED
        if args.verb == "validate-bundle":
            document = _load(_require(args.manifest, "manifest_required"))
            result = bundle_module.validate_bundle(
                _require(args.bundle, "bundle_required"), manifest=document,
                expected_request_id=args.request_id,
                now=_require(args.now, "timestamp_required"),
            )
            _emit({key: (list(value) if isinstance(value, tuple) else value)
                   for key, value in result.items()})
            return EXIT_OK if result["classification"] == "passed_live" else EXIT_BLOCKED
        document = _load(_require(args.manifest, "manifest_required"))
        store = ledger_module.ProofRunLedger(_require(args.ledger, "ledger_required"))
        record = store.read(_require(args.request_id, "request_id_required"))
        if record is None:
            _emit({"ok": False, "code": "run_unknown", "location": "ledger"})
            return EXIT_REFUSED
        validated_bundle = None
        if args.bundle is not None:
            validated_bundle = bundle_module.validate_bundle(
                _require(args.bundle, "bundle_required"), manifest=document,
                expected_request_id=record["request_id"],
                now=_require(args.now, "timestamp_required"),
            )
        built = report_module.build_report(
            manifest=document, record=record, bundle=validated_bundle)
        sys.stdout.write(report_module.render(built))
        return EXIT_OK if validated_bundle is not None \
            and built["classification"] == "passed_live" else EXIT_BLOCKED
    except (manifest_module.ManifestError, ledger_module.LedgerError,
            bundle_module.BundleError, probes_module.ProbeError,
            cleanup_module.CleanupError) as error:
        _emit(_bounded_failure(error))
        return EXIT_REFUSED
    except (OSError, ValueError, TypeError, KeyError):
        _emit({"ok": False, "code": "harness_failed", "location": "harness"})
        return EXIT_REFUSED


def main(argv: Any = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
