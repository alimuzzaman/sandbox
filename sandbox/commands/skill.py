from __future__ import annotations
import os
import re
import sys
from pathlib import Path

from sandbox.core import *  # noqa: F401,F403
from sandbox.registry import register

# Skill sources, in PRECEDENCE order (project > personal > sandbox) — spec 006.
_SANDBOX_SKILLS = ROOT / "skills"
_PERSONAL_SKILLS = Path.home() / ".claude" / "skills"


def _slugify(title: str) -> str:
    s = (title or "").strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _project_skills_dir() -> Path | None:
    """`.claude/skills` of the project rooted at cwd (walk up for a marker)."""
    cur = Path(os.getcwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / ".git").exists() or any(d.glob("sandbox.config.*")) or (d / ".claude").is_dir():
            return d / ".claude" / "skills"
        if d == d.parent:
            break
    return None


def _scope_root(scope: str) -> Path | None:
    if scope == "sandbox":
        return _SANDBOX_SKILLS
    if scope == "personal":
        return _PERSONAL_SKILLS
    if scope == "project":
        return _project_skills_dir()
    return None


def _parse_frontmatter(skill_md: Path) -> dict:
    name = desc = ""
    enable = True
    try:
        text = skill_md.read_text(errors="replace")
    except OSError:
        return {"name": name, "description": desc, "enable": enable}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                elif line.startswith("enable:"):
                    enable = line.split(":", 1)[1].strip().lower() not in ("false", "0", "no")
    return {"name": name, "description": desc, "enable": enable}


def _iter_source(scope: str):
    root = _scope_root(scope)
    if not root or not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        md = entry / "SKILL.md"
        if entry.is_dir() and md.is_file():
            meta = _parse_frontmatter(md)
            yield {"slug": entry.name, "scope": scope, "path": md,
                   "description": meta["description"], "enable": meta["enable"]}


def _resolve(include_disabled: bool = False) -> dict:
    """slug → winning record, precedence project > personal > sandbox."""
    out: dict[str, dict] = {}
    seen_roots: set[str] = set()
    for scope in ("sandbox", "personal", "project"):  # later overrides earlier
        root = _scope_root(scope)
        try:
            real = str(root.resolve()) if root else None
        except OSError:
            real = None
        if real and real in seen_roots:
            continue  # e.g. sandbox repo's .claude/skills symlinks to ./skills
        if real:
            seen_roots.add(real)
        for rec in _iter_source(scope):
            if not include_disabled and not rec["enable"]:
                continue
            out[rec["slug"]] = rec  # project written last → wins
    return out


def cmd_skill(cfg, args) -> None:
    action = args.action
    if action == "list":
        recs = _resolve(include_disabled=True)
        if not recs:
            info("no skills found")
            return
        for slug in sorted(recs):
            r = recs[slug]
            flag = "" if r["enable"] else " (disabled)"
            print(f"  {slug:<28} [{r['scope']}]{flag}  {r['description']}")
        return

    if action == "show":
        if not args.slug:
            die("usage: ./sb skill show <slug>")
        r = _resolve(include_disabled=True).get(args.slug)
        if not r:
            die(f"no skill '{args.slug}'")
        print(r["path"].read_text())
        return

    if action == "write":
        if not args.title or not args.desc:
            die("usage: ./sb skill write --title T --desc D [--scope project|personal|sandbox] [--file body.md|-] [--on-conflict fail|replace|rename]")
        slug = _slugify(args.title)
        if not slug:
            die(f"could not derive a slug from title {args.title!r}")
        scope = args.scope or ("project" if _project_skills_dir() else "sandbox")
        root = _scope_root(scope)
        if not root:
            die(f"scope '{scope}' unavailable here (no project root for cwd?)")
        # never silently shadow a built-in sandbox skill from another scope
        if scope != "sandbox" and (_SANDBOX_SKILLS / slug).is_dir():
            if args.on_conflict != "rename":
                die(f"'{slug}' shadows a built-in sandbox skill — use --on-conflict rename")
        dest = root / slug
        if dest.exists():
            if args.on_conflict == "fail" or not args.on_conflict:
                n = 2
                while (root / f"{slug}-{n}").exists():
                    n += 1
                die(f"skill '{slug}' exists in {scope} — pass --on-conflict replace|rename "
                    f"(free slug: {slug}-{n})")
            if args.on_conflict == "rename":
                n = 2
                while (root / f"{slug}-{n}").exists():
                    n += 1
                slug = f"{slug}-{n}"
                dest = root / slug
            # replace → fall through (overwrite)
        body = ""
        if args.file == "-":
            body = sys.stdin.read()
        elif args.file:
            body = Path(args.file).read_text()
        enable_line = "" if args.enable else "enable: false\n"
        content = (f"---\nname: {args.title}\ndescription: {args.desc}\n{enable_line}---\n\n"
                   f"{body or ('# ' + args.title + chr(10))}")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(content)
        ok(f"wrote skill '{slug}' ({scope}) → {(dest / 'SKILL.md')}")
        return

    if action == "edit":
        if not args.slug:
            die("usage: ./sb skill edit <slug> [--desc D] [--file body.md|-]")
        r = _resolve(include_disabled=True).get(args.slug)
        if not r:
            die(f"no skill '{args.slug}'")
        meta = _parse_frontmatter(r["path"])
        desc = args.desc or meta["description"]
        body = None
        if args.file == "-":
            body = sys.stdin.read()
        elif args.file:
            body = Path(args.file).read_text()
        if body is None:
            # keep existing body
            txt = r["path"].read_text()
            body = txt.split("\n---", 1)[1].split("\n", 1)[1] if txt.startswith("---") else txt
        en = "" if meta["enable"] else "enable: false\n"
        r["path"].write_text(f"---\nname: {meta['name'] or args.slug}\ndescription: {desc}\n{en}---\n{body}")
        ok(f"edited skill '{args.slug}' ({r['scope']})")
        return

    if action == "delete":
        if not args.slug:
            die("usage: ./sb skill delete <slug> [--scope …]")
        scope = args.scope
        recs = list(_iter_source(scope)) if scope else None
        r = None
        if scope:
            r = next((x for x in recs if x["slug"] == args.slug), None)
        else:
            r = _resolve(include_disabled=True).get(args.slug)
        if not r:
            die(f"no skill '{args.slug}'" + (f" in {scope}" if scope else ""))
        if r["scope"] == "sandbox" and scope != "sandbox":
            die(f"'{args.slug}' is a built-in sandbox skill — pass --scope sandbox to delete it")
        import shutil
        shutil.rmtree(r["path"].parent)
        ok(f"deleted skill '{args.slug}' ({r['scope']})")
        return

    die("usage: ./sb skill list|write|edit|delete|show")


register({'skill': cmd_skill})
