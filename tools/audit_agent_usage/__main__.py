"""Command-line boundary for the audit-only synthetic parser."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import parse_to_directory
from .validator import validate_output_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse one synthetic audit JSONL fixture into sanitized rows."
    )
    parser.add_argument("--input", required=True, type=Path, help="explicit JSONL input path")
    parser.add_argument(
        "--output-dir", required=True, type=Path, help="explicit audit-only output directory"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = parse_to_directory(args.input, args.output_dir)
        paths = (
            args.output_dir / "normalized.jsonl",
            args.output_dir / "exclusions.jsonl",
            args.output_dir / "accounting.json",
        )
        validation = validate_output_files(*paths)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}), file=sys.stderr)
        return 2
    if not validation.ok:
        print(json.dumps({"ok": False, "errors": list(validation.errors)}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "normalized": len(result.normalized),
                "exclusions": len(result.exclusions),
                "input_records": result.accounting["input_records"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
