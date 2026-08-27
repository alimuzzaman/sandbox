"""Deterministic, bounded report for a Credential Vault proof run.

The report's whole job is to keep four different things visibly apart: what the
local harness tested about itself, what a live run actually observed, what it
could not observe, and what a human still has to review. Collapsing those into
one "pass" line is the failure mode this exists to prevent.

No free-form exception text is ever printed. Every line is a stable code.
"""

from __future__ import annotations

from typing import Any

from . import ADOPTABLE, EVIDENCE_ID, SUPPORT_TIER
from .ledger import CLASSIFICATIONS


SECTIONS = (
    "harness_locally_tested", "live_checks_passed", "live_checks_failed",
    "checks_blocked", "evidence_missing", "cleanup_incomplete",
    "independent_review_pending",
)


def build_report(*, manifest: Any, record: Any, bundle: Any = None,
                 cleanup: Any = None, harness_tests: Any = ()) -> dict[str, Any]:
    """Assemble one deterministic report document from validated inputs."""
    checks = dict(record.get("checks", {})) if isinstance(record, dict) else {}
    required = tuple(item["check_id"] for item in manifest["checks"] if item["required"])
    optional = tuple(item["check_id"] for item in manifest["checks"]
                     if not item["required"])
    live = record.get("provenance") == "live_authorized_host" if isinstance(record, dict) else False
    passed = tuple(sorted(name for name, state in checks.items() if state == "passed"))
    failed = tuple(sorted(name for name, state in checks.items() if state == "failed"))
    blocked = tuple(sorted(name for name, state in checks.items()
                           if state in {"blocked", "skipped", "pending"}))
    recorded_artifacts = set(record.get("artifacts", {})) if isinstance(record, dict) else set()
    missing = tuple(sorted(
        item["name"] for item in manifest["artifacts"]
        if item["name"] not in recorded_artifacts
    ))
    classification = record.get("classification") if isinstance(record, dict) else None
    if classification is not None and classification not in CLASSIFICATIONS:
        classification = None
    cleanup_state = record.get("cleanup_state") if isinstance(record, dict) else None
    retained = tuple(
        f"{item['kind']}:{item['identity']}:{item['reason_code']}"
        for item in (cleanup or {}).get("retained", ())
    ) if isinstance(cleanup, dict) else ()
    review_pending = (
        "t031_independent_review",
        *(("t022_helper_service_proof",) if not live else ()),
        *(("t029_live_feature_matrix",) if classification != "passed_live" else ()),
    )
    return {
        "version": 1,
        "support_tier": SUPPORT_TIER,
        "adoptable": ADOPTABLE,
        "evidence_id": EVIDENCE_ID,
        "manifest_id": manifest["manifest_id"],
        "manifest_digest": (bundle or {}).get("manifest_digest")
        if isinstance(bundle, dict) else None,
        "request_id": record.get("request_id") if isinstance(record, dict) else None,
        "provenance": record.get("provenance") if isinstance(record, dict) else None,
        "classification": classification,
        "required_check_count": len(required),
        "optional_check_count": len(optional),
        "harness_locally_tested": tuple(sorted(str(item)[:64] for item in harness_tests)),
        "live_checks_passed": passed if live else (),
        "live_checks_failed": failed if live else (),
        "checks_blocked": blocked if live else tuple(sorted(checks)),
        "evidence_missing": missing,
        "cleanup_incomplete": retained if cleanup_state != "complete" else (),
        "independent_review_pending": review_pending,
    }


def render(report: Any) -> str:
    """Render one stable, line-oriented text report with no free-form text."""
    if not isinstance(report, dict):
        return "credential-vault-proof: report_invalid\n"
    lines = [
        "credential-vault-proof report",
        f"  support_tier: {report.get('support_tier')}",
        f"  adoptable: {str(bool(report.get('adoptable'))).lower()}",
        f"  evidence_id: {report.get('evidence_id') if report.get('evidence_id') else 'null'}",
        f"  manifest_id: {report.get('manifest_id')}",
        f"  manifest_digest: {report.get('manifest_digest') or 'null'}",
        f"  request_id: {report.get('request_id') or 'null'}",
        f"  provenance: {report.get('provenance') or 'null'}",
        f"  classification: {report.get('classification') or 'none'}",
    ]
    for section in SECTIONS:
        values = tuple(report.get(section, ()) or ())
        lines.append(f"  {section}: {len(values)}")
        for value in values:
            lines.append(f"    - {value}")
    lines.append("  note: local harness tests are not live proof")
    return "\n".join(lines) + "\n"


__all__ = ["SECTIONS", "build_report", "render"]
