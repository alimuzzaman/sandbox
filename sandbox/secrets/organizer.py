"""Group a registered dotenv source into documented sections.

The organizer never parses, prints, logs, or rewrites a value.  It reorders the
raw assignment records produced by :mod:`sandbox.secrets.parser` and emits
generated banner comments around them, so every byte of every assignment line
survives the rewrite unchanged.  Reporting is key names and counts only.

Group membership is decided from key names alone.  Unrecognized keys keep their
content and collect in a trailing "Ungrouped" section rather than being dropped
or guessed at.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .parser import AssignmentRecord, CommentRecord, ParsedDocument, SecretParseError


RULE = "#" * 78
BANNER_NOTE = (
    "Grouped and documented by `sb secrets organize` - rerun it after adding keys.",
    "Sections are ordered by owner: this machine, then product, then service.",
    "Values are moved as opaque text; the organizer never reads or rewrites one.",
)
_KEYISH = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


@dataclass(frozen=True)
class SecretGroup:
    identifier: str
    title: str
    blurb: tuple[str, ...] = ()
    keys: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()


GROUPS: tuple[SecretGroup, ...] = (
    SecretGroup(
        identifier="sandbox-recovery",
        title="Sandbox recovery",
        blurb=(
            "Passphrase protecting Sandbox capture/restore material.",
            "Losing it makes existing protected captures unrecoverable; rotate it",
            "only together with a documented re-encryption pass.",
        ),
        keys=("RECOVERY_PASSPHRASE",),
    ),
    SecretGroup(
        identifier="templately-api",
        title="Templately API keys",
        blurb=(
            "Destination-bound plugin API keys.",
            "A _DEV key belongs to app.templately.dev only and a production key to",
            "app.templately.com only; never cross the two environments.",
            "Exploratory or verification calls use the dev pair.",
        ),
        prefixes=("TEMPLATELY_API_KEY",),
    ),
    SecretGroup(
        identifier="templately-hosting",
        title="Templately hosted environments",
        blurb=("Access gates for Templately-owned deployments and kits.",),
        keys=("FSI_KIT_TOKEN",),
        prefixes=("TEMPLATELY_",),
    ),
    SecretGroup(
        identifier="lenzora-development",
        title="Lenzora - development",
        blurb=(
            "Non-production Lenzora stack credentials.",
            "The agent-auth hash and signing secrets are deliberately distinct.",
            "Do not point any of these at a production tenant.",
        ),
        prefixes=("LENZORA_DEVELOPMENT_",),
    ),
    SecretGroup(
        identifier="lenzora-production",
        title="Lenzora - production",
        blurb=(
            "Production Lenzora credentials. Rotation is a deployment-owned action.",
            "WEBHOOK/ENCRYPTION-class values cannot be replaced without re-encrypting",
            "the records they protect.",
        ),
        prefixes=("LENZORA_PRODUCTION_",),
    ),
    SecretGroup(
        identifier="lenzora-shared",
        title="Lenzora - unscoped WorkOS wiring",
        blurb=(
            "Environment-less WorkOS values read by local runs.",
            "WORKOS_REDIRECT_URI must exist verbatim in the WorkOS application",
            "Redirects settings.",
        ),
        prefixes=("WORKOS_", "LENZORA_"),
    ),
    SecretGroup(
        identifier="hosted-wordpress",
        title="Hosted WordPress (ASB production)",
        blurb=(
            "Admin, database, and access-gate credentials for the hosted site.",
            "Database root and admin values are separate on purpose.",
        ),
        prefixes=("ASB_",),
    ),
    SecretGroup(
        identifier="personal-sites",
        title="Personal site access gates",
        blurb=("HTTP basic-auth gates for personal and staging deployments.",),
        keys=("BASIC_AUTH_PASSWORD",),
        prefixes=("ALIMUZZAMAN_",),
    ),
    SecretGroup(
        identifier="edge",
        title="Cloudflare and tunnels",
        blurb=(
            "DNS, account, and tunnel connector credentials.",
            "The scoped token is preferred; the full-access token is a fallback and",
            "should be the first thing rotated after any exposure.",
        ),
        prefixes=("CLOUDFLARE_", "HERMES_"),
    ),
    SecretGroup(
        identifier="publishing",
        title="Code and package publishing",
        blurb=(
            "Tokens that can publish or mutate a public artifact.",
            "Treat every one of these as release authority, not read access.",
        ),
        keys=("GH_FINE_GRAINED_TOKEN", "NODE_AUTH_TOKEN"),
        prefixes=("WP_ORG_",),
    ),
    SecretGroup(
        identifier="services",
        title="Third-party service credentials",
        blurb=("Provider keys used by tooling rather than by a deployed product.",),
        prefixes=("GOOGLE_", "XCLOUD_", "OPENAI_", "ANTHROPIC_"),
    ),
)

UNGROUPED = SecretGroup(
    identifier="ungrouped",
    title="Ungrouped",
    blurb=(
        "Keys with no registered group. Add one to `sandbox/secrets/organizer.py`",
        "so the next run files them by owner.",
    ),
)
NOTES = SecretGroup(
    identifier="notes",
    title="Notes",
    blurb=("Comment blocks that name no known key.",),
)


def classify(key: str) -> SecretGroup | None:
    """Return the owning group: exact match wins, then the longest prefix."""
    best: SecretGroup | None = None
    best_length = -1
    for group in GROUPS:
        if key in group.keys:
            return group
        for prefix in group.prefixes:
            if key.startswith(prefix) and len(prefix) > best_length:
                best, best_length = group, len(prefix)
    return best


def _classify_comment(lines: tuple[str, ...]) -> SecretGroup | None:
    for line in lines:
        for match in _KEYISH.finditer(line):
            group = classify(match.group(1))
            if group is not None:
                return group
    return None


@dataclass
class _Item:
    kind: str
    key: str | None
    body: str | None
    comments: tuple[str, ...] = ()


@dataclass
class OrganizeReport:
    changed: bool
    content: bytes
    count: int
    groups: list[tuple[str, list[str]]] = field(default_factory=list)


def _strip_generated_banners(records) -> list:
    """Drop banner blocks a previous run emitted so they are not re-absorbed."""
    kept: list = []
    index = 0
    while index < len(records):
        record = records[index]
        if not (isinstance(record, CommentRecord) and record.text.strip() == RULE):
            kept.append(record)
            index += 1
            continue
        scan = index + 1
        while (scan < len(records) and isinstance(records[scan], CommentRecord)
               and records[scan].text.strip() != RULE):
            scan += 1
        if scan < len(records) and isinstance(records[scan], CommentRecord):
            index = scan + 1  # skip the opening rule, the body, and the closing rule
            continue
        kept.append(record)
        index += 1
    return kept


def _collect(document: ParsedDocument) -> list[_Item]:
    items: list[_Item] = []
    pending: list[str] = []
    for record in _strip_generated_banners(list(document.records)):
        if isinstance(record, AssignmentRecord):
            items.append(_Item("assignment", record.key, record.text, tuple(pending)))
            pending = []
        elif isinstance(record, CommentRecord):
            pending.append(record.text.strip())
        else:  # blank line: a detached comment block ends here
            if pending:
                items.append(_Item("note", None, None, tuple(pending)))
                pending = []
    if pending:
        items.append(_Item("note", None, None, tuple(pending)))
    return items


def _fingerprint(items: list[_Item]) -> str:
    """Order-independent digest of every assignment line, values never compared."""
    lines = sorted(item.body or "" for item in items if item.kind == "assignment")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _render(items: list[_Item], newline: str) -> str:
    buckets: dict[str, list[_Item]] = {}
    for item in items:
        group = (
            _classify_comment(item.comments) or NOTES
            if item.kind == "note"
            else classify(item.key or "") or UNGROUPED
        )
        buckets.setdefault(group.identifier, []).append(item)

    out: list[str] = [RULE, "# Personal secrets. Keep this file out of Git.", "#"]
    out.extend(f"# {line}" for line in BANNER_NOTE)
    out.append(RULE)

    for group in (*GROUPS, UNGROUPED, NOTES):
        bucket = buckets.get(group.identifier)
        if not bucket:
            continue
        out.extend(("", RULE, f"# {group.title}"))
        out.extend(f"# {line}" for line in group.blurb)
        out.append(RULE)
        previous = None
        for item in bucket:
            if previous is not None and item.comments:
                out.append("")
            out.extend(item.comments)
            if item.body is not None:
                out.append(item.body)
            previous = item
    out.append("")
    return newline.join(out)


def organize(document: ParsedDocument) -> OrganizeReport:
    """Return the grouped rendering of ``document`` plus a name-only summary."""
    if document.newline_style == "mixed":
        raise SecretParseError("mixed_newlines")
    newline = "\r\n" if document.newline_style == "crlf" else "\n"
    items = _collect(document)
    content = _render(items, newline)

    # Round-trip proof: reparsing the rendering must yield the identical set of
    # assignment lines. Refuse to hand back anything that fails this.
    from .parser import parse_document

    if _fingerprint(_collect(parse_document(content.encode("utf-8")))) != _fingerprint(items):
        raise SecretParseError("round_trip_failed")

    summary: dict[str, list[str]] = {}
    for item in items:
        if item.kind != "assignment":
            continue
        group = classify(item.key or "") or UNGROUPED
        summary.setdefault(group.title, []).append(item.key or "")
    ordered_titles = [group.title for group in (*GROUPS, UNGROUPED)]
    groups = [(title, sorted(summary[title])) for title in ordered_titles if title in summary]

    encoded = content.encode("utf-8")
    return OrganizeReport(
        changed=encoded != document.raw_bytes,
        content=encoded,
        count=sum(len(keys) for _, keys in groups),
        groups=groups,
    )


__all__ = ["GROUPS", "OrganizeReport", "SecretGroup", "classify", "organize"]
