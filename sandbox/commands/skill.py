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


def _project_skills_dir(project_dir: str | os.PathLike[str] | None = None) -> Path | None:
    """Return a project's ``.claude/skills`` root without requiring a stack.

    MCP callers provide the target project explicitly; CLI callers retain the
    convenient cwd-based discovery.  Authoring intentionally does not use the
    instance-gated focus tool, because skills must work before a stack exists.
    """
    if project_dir:
        try:
            return Path(project_dir).expanduser().resolve() / ".claude" / "skills"
        except OSError:
            return None
    cur = Path(os.getcwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / ".git").exists() or any(d.glob("sandbox.config.*")) or (d / ".claude").is_dir():
            return d / ".claude" / "skills"
        if d == d.parent:
            break
    return None


def _scope_root(scope: str, project_dir: str | os.PathLike[str] | None = None) -> Path | None:
    if scope == "sandbox":
        return _SANDBOX_SKILLS
    if scope == "personal":
        return _PERSONAL_SKILLS
    if scope == "project":
        return _project_skills_dir(project_dir)
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


def _within(path: Path, root: Path) -> bool:
    """Whether a resolved skill path remains inside its declared scope root."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _iter_source(scope: str, project_dir: str | os.PathLike[str] | None = None):
    root = _scope_root(scope, project_dir)
    if not root or not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        md = entry / "SKILL.md"
        if entry.is_dir() and md.is_file() and _within(md, root):
            meta = _parse_frontmatter(md)
            yield {"slug": entry.name, "scope": scope, "path": md,
                   "description": meta["description"], "enable": meta["enable"]}


def _resolve(include_disabled: bool = False,
             project_dir: str | os.PathLike[str] | None = None) -> dict:
    """slug → winning record, precedence project > personal > sandbox."""
    out: dict[str, dict] = {}
    seen_roots: set[str] = set()
    for scope in ("sandbox", "personal", "project"):  # later overrides earlier
        root = _scope_root(scope, project_dir)
        try:
            real = str(root.resolve()) if root else None
        except OSError:
            real = None
        if real and real in seen_roots:
            continue  # e.g. sandbox repo's .claude/skills symlinks to ./skills
        if real:
            seen_roots.add(real)
        for rec in _iter_source(scope, project_dir):
            if not include_disabled and not rec["enable"]:
                continue
            out[rec["slug"]] = rec  # project written last → wins
    return out


def cmd_skill(cfg, args) -> None:
    action = args.action
    if action == "list":
        recs = _resolve()
        if not recs:
            info("no skills found")
            return
        for slug in sorted(recs):
            r = recs[slug]
            print(f"  {slug:<28} [{r['scope']}]  {r['description']}")
        return

    if action == "show":
        if not args.slug:
            die("usage: ./sb skill show <slug>")
        r = _resolve().get(args.slug)
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
        # Project/personal skills must never shadow a built-in silently.
        if scope != "sandbox" and (_SANDBOX_SKILLS / slug).is_dir():
            if args.on_conflict != "rename":
                die(f"'{slug}' shadows a built-in sandbox skill — use --on-conflict rename")
        dest = root / slug
        if dest.exists():
            if scope == "sandbox" and args.on_conflict == "replace":
                die(f"cannot replace built-in sandbox skill '{slug}' — use --on-conflict rename")
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
        if args.scope:
            r = next((x for x in _iter_source(args.scope) if x["slug"] == args.slug), None)
        else:
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
