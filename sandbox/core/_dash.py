from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr


def collect_instance_rows(cfg: dict) -> list[dict]:
    """Per-instance view-model shared by `cmd_instances` (static print) and the
    `dashboard` TUI, so the two never drift. One dict per instance with status,
    URLs, server, MCP server name, project, and focus.
    """
    sc = _core()
    rows = []
    local_cfg = _local_yaml()
    for name, inst_cfg in resolve_instances(cfg).items():
        ff = focus_file(name)
        # Per-project model: the project is the registry root this instance
        # serves (its dir basename), not the vestigial .active-project file.
        owner = sc.registry_find_instance(name)
        project = Path(owner["root"]).name if owner and owner.get("root") else "—"
        _base = site_url(inst_cfg)
        _token = local_cfg.get("instances", {}).get(name, {}).get("autologin_token", "")
        rows.append({
            "name": name,
            "running": _instance_running(name),
            "wordpress_port": inst_cfg["wordpress_port"],
            "mailpit_port": inst_cfg["mailpit_port"],
            "url": _base,
            "admin_url": f"{_base}/wp-admin/",
            "login_url": f"{_base}/?sandbox_autologin={_token}" if _token else "",
            "domain": inst_cfg.get("domain"),
            "server": inst_cfg.get("server", "apache"),
            "mcp_server": mcp_server_name(name),
            "project": project,
            "focus": ff.read_text().strip() if ff.exists() else "—",
        })
    return rows


def domains_ready() -> bool:
    """True once custom domains can be applied without a password. Primary path
    is the sandbox HTTPS proxy (cert generated). Fallbacks: Valet, or the
    one-time passwordless /etc/hosts sudoers rule."""
    return proxy_available() or _valet_available() or _hosts_passwordless()


def _curses_suspended(stdscr):
    """Drop out of curses to run normal terminal I/O (a cmd_* with its prints,
    or an input() prompt), then restore the full-screen UI."""
    import curses
    curses.def_prog_mode()      # save curses tty state
    curses.endwin()             # back to normal terminal
    try:
        yield
    finally:
        stdscr.refresh()        # restore saved screen
        curses.reset_prog_mode()
        stdscr.clear()


def _dash_prompt(stdscr, label: str) -> str:
    """One-line text prompt at the bottom of the screen. Returns the entry
    (stripped); empty string if cancelled with Esc/blank."""
    import curses
    h, w = stdscr.getmaxyx()
    curses.echo()
    curses.curs_set(1)
    stdscr.move(h - 1, 0)
    stdscr.clrtoeol()
    stdscr.addstr(h - 1, 0, label[:w - 1])
    stdscr.refresh()
    try:
        raw = stdscr.getstr(h - 1, len(label), max(1, w - len(label) - 1))
        val = raw.decode("utf-8", "replace").strip() if raw else ""
    except Exception:
        val = ""
    finally:
        curses.noecho()
        curses.curs_set(0)
    return val


def _dash_pick(stdscr, label: str, options: list[str]) -> str | None:
    """Inline single-key picker: shows `label: [a]pache [n]ginx ...` and
    returns the option whose first letter is pressed, or None on Esc."""
    import curses
    h, w = stdscr.getmaxyx()
    hint = label + "  " + "  ".join(f"[{o[0]}]{o[1:]}" for o in options)
    stdscr.move(h - 1, 0)
    stdscr.clrtoeol()
    stdscr.addstr(h - 1, 0, hint[:w - 1])
    stdscr.refresh()
    while True:
        c = stdscr.getch()
        if c in (27, ord("q")):
            return None
        for o in options:
            if c == ord(o[0]):
                return o


def _dash_flash(stdscr, msg: str):
    """Transient status line message (shown until next redraw)."""
    import curses
    h, w = stdscr.getmaxyx()
    try:
        stdscr.move(h - 1, 0)
        stdscr.clrtoeol()
        stdscr.addstr(h - 1, 0, msg[:w - 1], curses.A_BOLD)
        stdscr.refresh()
    except Exception:
        pass


def _dash_draw(stdscr, rows: list[dict], selected: int):
    import curses
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    title = f" Sandbox Dashboard — {len(rows)} instance(s) "
    stdscr.addstr(0, 0, title[:w - 1], curses.A_BOLD)
    header = (f"  {'STATUS':<9}{'NAME':<12}{'URL':<26}{'SERVER':<11}"
              f"{'MCP SERVER':<19}{'PROJECT':<12}FOCUS")
    stdscr.addstr(2, 0, header[:w - 1], curses.A_UNDERLINE)
    for i, r in enumerate(rows):
        y = 3 + i
        if y >= h - 2:
            break
        dot = "● run " if r["running"] else "○ stop"
        line = (f"  {dot:<9}{r['name']:<12}{r['url']:<26}{r['server']:<11}"
                f"{r['mcp_server']:<19}{r['project']:<12}{r['focus']}")
        attr = curses.A_REVERSE if i == selected else 0
        if not r["running"]:
            attr |= curses.A_DIM
        stdscr.addstr(y, 0, line[:w - 1], attr)
    legend = ("↑↓ move · s start · x stop · R restart · o open · f focus · "
              "n new · d delete · r refresh · q quit")
    try:
        stdscr.addstr(h - 2, 0, legend[:w - 1], curses.A_DIM)
    except Exception:
        pass
    stdscr.refresh()


def _dash_run(stdscr, cfg):
    from sandbox.commands.lifecycle import cmd_up, cmd_down, cmd_open
    from sandbox.commands.instances_cmd import cmd_focus, cmd_instance
    import curses
    curses.curs_set(0)
    stdscr.timeout(2000)        # getch returns -1 every ~2s → auto-refresh
    selected = 0
    rows = collect_instance_rows(cfg)

    def reload_rows():
        nonlocal cfg, rows
        cfg = load_config()
        rows = collect_instance_rows(cfg)

    while True:
        if rows:
            selected = max(0, min(selected, len(rows) - 1))
        _dash_draw(stdscr, rows, selected)
        c = stdscr.getch()

        if c == -1:                       # timeout tick → refresh
            reload_rows()
            continue
        if c in (ord("q"), 27):
            return
        if c in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
            continue
        if c in (curses.KEY_DOWN, ord("j")):
            selected = min(len(rows) - 1, selected + 1) if rows else 0
            continue
        if c in (ord("r"),):
            reload_rows()
            continue
        if c == curses.KEY_RESIZE:
            continue

        sel = rows[selected] if rows else None

        if c == ord("n"):                 # new instance → per-project model
            _dash_flash(stdscr, "Create instances with `./sb init` inside a "
                                "plugin repo (per-project). Press a key…")
            stdscr.getch()
            continue

        if not sel:
            continue

        name = sel["name"]
        if c == ord("s"):                 # start
            with _curses_suspended(stdscr):
                cmd_up(cfg, _types.SimpleNamespace(resolved_instance=name))
            reload_rows()
        elif c == ord("x"):               # stop
            with _curses_suspended(stdscr):
                cmd_down(cfg, _types.SimpleNamespace(resolved_instance=name))
            reload_rows()
        elif c == ord("R"):               # restart
            with _curses_suspended(stdscr):
                compose("restart", "wp", instance=name, check=False)
            reload_rows()
        elif c == ord("o"):               # open admin/site/mail
            what = _dash_pick(stdscr, "open:",
                              ["admin", "site", "mail"])
            if what:
                cmd_open(cfg, _types.SimpleNamespace(
                    resolved_instance=name, what=what))
        elif c == ord("f"):               # set focus
            slug = _dash_prompt(stdscr, f"focus plugin for '{name}': ")
            if slug:
                with _curses_suspended(stdscr):
                    cmd_focus(load_config(), _types.SimpleNamespace(
                        resolved_instance=name, slug=slug, clear=False))
                    input("\n[enter] to return…")
                reload_rows()
        elif c == ord("d"):               # delete (confirm)
            typed = _dash_prompt(stdscr, f"type '{name}' to delete: ")
            if typed == name:
                with _curses_suspended(stdscr):
                    cmd_instance(load_config(), _types.SimpleNamespace(
                        action="delete", name=name, yes=True,
                        resolved_instance=name))
                    input("\n[enter] to return…")
                selected = 0
                reload_rows()


class _JobStream:
    """File-like sink that appends every write to a job's output buffer under
    lock, so the web console can poll incremental output while a command runs.
    """
    def __init__(self, job):
        self._job = job

    def write(self, s):
        if s:
            with _web_jobs_lock:
                self._job["output"] += s
        return len(s)

    def flush(self):
        pass


def _start_job(label: str, fn) -> str:
    """Run `fn(stream)` in a background thread, streaming its output into a new
    job. `fn` returns True/False for ok. Returns the job_id immediately so the
    page can poll /api/job/<id>?offset=N for live output."""
    _web_job_seq[0] += 1
    job_id = str(_web_job_seq[0])
    job = {"status": label, "output": "", "done": False, "ok": None}
    _web_jobs[job_id] = job
    stream = _JobStream(job)

    def worker():
        ok_flag = True
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                _WEB_STREAM[0] = True       # stream real subprocess output → console
                try:
                    ok_flag = fn() is not False
                finally:
                    _WEB_STREAM[0] = False
        except SystemExit as e:
            ok_flag = (str(e) in ("0", "None"))
        except Exception as e:
            ok_flag = False
            stream.write(f"\nerror: {e}\n")
        with _web_jobs_lock:
            job["done"] = True
            job["ok"] = ok_flag
            job["status"] = label + (" ✓" if ok_flag else " ✗")

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def _job_snapshot(job_id: str, offset: int = 0) -> dict | None:
    """Return a job's state with only output past `offset` (incremental)."""
    job = _web_jobs.get(job_id)
    if job is None:
        return None
    with _web_jobs_lock:
        full = job["output"]
        return {"status": job["status"], "done": job["done"], "ok": job["ok"],
                "offset": len(full), "chunk": full[offset:] if offset < len(full) else ""}


def _web_list_snapshots(instance: str) -> list[str]:
    """Snapshot names saved for an instance (for the restore picker)."""
    d = snapshots_dir(instance)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def _web_list_seeds() -> list[str]:
    """WXR files available under runtime/seeds/ (for the seed picker)."""
    if not SEEDS_DIR.exists():
        return []
    return sorted(p.name for p in SEEDS_DIR.iterdir()
                  if p.is_file() and p.suffix in (".xml", ".wxr"))


def _price_tier(model: str) -> str:
    m = (model or "").lower()
    if "opus" in m: return "opus"
    if "haiku" in m: return "haiku"
    return "sonnet"


def _cost_for(tier: str, u: dict) -> float:
    p = _CLAUDE_PRICES[tier]
    return (u["in"]*p["in"] + u["out"]*p["out"]
            + u["cw"]*p["cw"] + u["cr"]*p["cr"]) / 1_000_000


def _claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def claude_usage(known_instances: list[str]) -> dict:
    """Aggregate Claude token usage + estimated cost across all session
    transcripts, with a best-effort per-instance breakdown.

    Per-project model: there's ONE `sandbox` MCP server, so the tool NAMESPACE
    no longer encodes the instance — attribution is by each sandbox tool call's
    `project_dir` argument, mapped to an instance via the registry.

    Returns {total, by_model, per_instance, sessions:[...recent...], generated}.
    Resilient: skips unreadable lines/files; never raises."""
    pdir = _claude_projects_dir()
    blank = lambda: {"in": 0, "out": 0, "cw": 0, "cr": 0}
    # project root (canonical) -> instance, for mapping a tool's project_dir.
    sc = _core()
    root_to_inst = {r: e.get("instance")
                    for r, e in sc.registry_all().items() if e.get("instance")}

    def _pd_to_inst(pd):
        if not pd:
            return None
        try:
            root = str(sc.find_project_root(pd))
        except Exception:
            try:
                root = str(Path(pd).expanduser().resolve())
            except Exception:
                return None
        return root_to_inst.get(root)

    total = blank(); by_model = {}; per_instance = {}; sessions = []
    if not pdir.exists():
        return {"total": total, "tokens": 0, "cost": 0.0, "by_model": {},
                "per_instance": {}, "sessions": [], "available": False}

    for proj in pdir.iterdir():
        if not proj.is_dir():
            continue
        for tf in proj.glob("*.jsonl"):
            su = blank(); s_models = set(); s_dirs = set(); s_used = False
            s_mtime = tf.stat().st_mtime
            try:
                for line in tf.open(errors="ignore"):
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    msg = o.get("message") or {}
                    u = msg.get("usage")
                    if u:
                        su["in"] += u.get("input_tokens", 0) or 0
                        su["out"] += u.get("output_tokens", 0) or 0
                        su["cw"] += u.get("cache_creation_input_tokens", 0) or 0
                        su["cr"] += u.get("cache_read_input_tokens", 0) or 0
                        if msg.get("model"):
                            s_models.add(msg["model"])
                    for blk in (msg.get("content") or []):
                        if isinstance(blk, dict) and blk.get("type") == "tool_use" \
                                and str(blk.get("name", "")).startswith("mcp__sandbox"):
                            s_used = True
                            pd = (blk.get("input") or {}).get("project_dir")
                            if pd:
                                s_dirs.add(pd)
            except OSError:
                continue
            if not any(su.values()):
                continue
            # Only count sessions that touched a sandbox tool OR ran from a
            # sandbox project dir (transcript dir name encodes the cwd).
            touched_sandbox = s_used or "sandbox" in proj.name.lower()
            if not touched_sandbox:
                continue

            tier = _price_tier(next(iter(s_models), ""))
            cost = _cost_for(tier, su)
            for k in total: total[k] += su[k]
            bm = by_model.setdefault(tier, blank())
            for k in bm: bm[k] += su[k]
            # Attribute to each instance whose project_dir this session drove;
            # sandbox-touching sessions with no resolvable project go to
            # 'unattributed' rather than silently onto one instance.
            insts = sorted({i for i in (_pd_to_inst(d) for d in s_dirs) if i})
            targets = insts or ["unattributed"]
            for inst in targets:
                pi = per_instance.setdefault(inst, {**blank(), "cost": 0.0})
                for k in su: pi[k] += su[k]
                pi["cost"] += cost / len(targets)
            sessions.append({
                "id": tf.stem[:8], "model": tier, "mtime": s_mtime,
                "tokens": sum(su.values()), "cost": round(cost, 4),
                "instances": insts,
            })

    total_tokens = sum(total.values())
    total_cost = sum(_cost_for(t, by_model[t]) for t in by_model)
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    by_model_out = {t: {**by_model[t], "cost": round(_cost_for(t, by_model[t]), 4)}
                    for t in by_model}
    per_instance_out = {i: {**v, "cost": round(v["cost"], 4)}
                        for i, v in per_instance.items()}
    return {
        "available": True,
        "total": total, "tokens": total_tokens, "cost": round(total_cost, 4),
        "by_model": by_model_out, "per_instance": per_instance_out,
        "sessions": sessions[:25],
    }


def _web_do_action(payload: dict) -> dict:
    from sandbox.commands.lifecycle import cmd_up, cmd_down, cmd_status, cmd_update, cmd_doctor
    from sandbox.commands.instances_cmd import cmd_focus, cmd_instance
    from sandbox.commands.debug import cmd_introspect, cmd_xdebug
    from sandbox.commands.data import cmd_restore, cmd_snapshot
    from sandbox.commands.wp import cmd_seed, cmd_wp
    from sandbox.commands.net import cmd_server
    """Dispatch a UI action to the matching cmd_*. Fast actions return output
    inline; create/delete (and the all-* sweeps) return a job_id and run in a
    background thread."""
    action = payload.get("action")
    name = (payload.get("instance") or "").strip()
    valid_fast = {"start", "stop", "restart", "focus", "unfocus"}

    if action in valid_fast:
        if not name:
            return {"ok": False, "output": "missing instance"}
        with _web_lock:
            if action == "start":
                ok_f, out = _run_cmd_capture(
                    cmd_up, _types.SimpleNamespace(resolved_instance=name))
            elif action == "stop":
                ok_f, out = _run_cmd_capture(
                    cmd_down, _types.SimpleNamespace(resolved_instance=name))
            elif action == "restart":
                out_buf = io.StringIO()
                with redirect_stdout(out_buf), redirect_stderr(out_buf):
                    compose("restart", "wp", instance=name, check=False)
                ok_f, out = True, out_buf.getvalue()
            elif action == "focus":
                slug = (payload.get("slug") or "").strip()
                if not slug:
                    return {"ok": False, "output": "missing plugin slug"}
                ok_f, out = _run_cmd_capture(cmd_focus, _types.SimpleNamespace(
                    resolved_instance=name, slug=slug, clear=False))
            elif action == "unfocus":
                ok_f, out = _run_cmd_capture(cmd_focus, _types.SimpleNamespace(
                    resolved_instance=name, slug=None, clear=True))
        return {"ok": ok_f, "output": out}

    # Sweep actions over every instance — backgrounded (booting all stacks is
    # slow). The page polls the job and refreshes when done.
    if action in ("start-all", "stop-all"):
        _web_job_seq[0] += 1
        job_id = str(_web_job_seq[0])
        _web_jobs[job_id] = {"status": f"{action}…", "output": "",
                             "done": False, "ok": None}

        def sweep():
            with _web_lock:
                cfg = load_config()
                names = list(resolve_instances(cfg).keys())
                fn = cmd_up if action == "start-all" else cmd_down
                buf = io.StringIO()
                allok = True
                for n in names:
                    with redirect_stdout(buf), redirect_stderr(buf):
                        try:
                            fn(cfg, _types.SimpleNamespace(resolved_instance=n))
                        except Exception as e:
                            allok = False
                            buf.write(f"\n{n}: error {e}\n")
                    buf.write(f"— {action} {n} done\n")
                _web_jobs[job_id].update(
                    output=buf.getvalue(), done=True, ok=allok,
                    status=f"{action} {'✓' if allok else '✗'}")

        threading.Thread(target=sweep, daemon=True).start()
        return {"ok": True, "job_id": job_id}

    # Ops / terminal actions — each streams output into a job the console
    # panel tails. All read or scoped to one instance; destructive ones
    # (restore) are confirmed client-side. `shell`/`claude` are intentionally
    # NOT exposed (interactive, can't work over HTTP).
    OPS = {"logs", "status", "doctor", "snapshot", "restore", "seed",
           "update", "xdebug", "wp", "introspect", "install", "term"}
    if action in OPS:
        if not name:
            return {"ok": False, "output": "missing instance"}
        ns_base = {"resolved_instance": name}

        def run_op():
            cfg = load_config()
            if action == "logs":
                # Non-following snapshot of recent logs (the -f variant would
                # never return). Tail the last N lines of wp+db.
                _compose_no_follow_logs(name)
            elif action == "status":
                cmd_status(cfg, _types.SimpleNamespace(**ns_base))
            elif action == "doctor":
                cmd_doctor(cfg, _types.SimpleNamespace(**ns_base))
            elif action == "update":
                cmd_update(cfg, _types.SimpleNamespace(**ns_base))
            elif action == "introspect":
                cmd_introspect(cfg, _types.SimpleNamespace(
                    target=payload.get("target") or "all", **ns_base))
            elif action == "xdebug":
                state = payload.get("state") or "status"
                if state not in ("on", "off", "status"):
                    print("invalid xdebug state"); return False
                cmd_xdebug(cfg, _types.SimpleNamespace(state=state, **ns_base))
            elif action == "snapshot":
                snap = (payload.get("name") or "").strip()
                if not re.match(r"^[a-z0-9][a-z0-9_-]{0,40}$", snap):
                    print("invalid snapshot name"); return False
                cmd_snapshot(cfg, _types.SimpleNamespace(
                    name=snap, force=bool(payload.get("force")), **ns_base))
            elif action == "restore":
                snap = (payload.get("name") or "").strip()
                if not snap:
                    print("missing snapshot name"); return False
                cmd_restore(cfg, _types.SimpleNamespace(name=snap, **ns_base))
            elif action == "seed":
                f = (payload.get("file") or "").strip()
                if not f:
                    print("missing seed file"); return False
                cmd_seed(cfg, _types.SimpleNamespace(file=f, **ns_base))
            elif action == "wp":
                argstr = (payload.get("args") or "").strip()
                if not argstr:
                    print("missing wp-cli args"); return False
                import shlex as _shlex
                cmd_wp(cfg, _types.SimpleNamespace(
                    passthrough=_shlex.split(argstr), **ns_base))
            elif action == "install":
                slug = (payload.get("slug") or "").strip()
                if not re.match(r"^[a-z0-9][a-z0-9.-]{0,60}$", slug):
                    print("invalid plugin slug"); return False
                # Install from the wp.org directory + activate (streamed).
                wpcli(["plugin", "install", slug, "--activate"], instance=name)
            elif action == "term":
                # Interactive terminal: run a command INSIDE the instance's
                # container (not the host). `wp ...` → wpcli container; anything
                # else → shell in the wp container. Streamed live.
                line = (payload.get("cmd") or "").strip()
                if not line:
                    print("(empty)"); return True
                import shlex as _shlex
                if line == "wp" or line.startswith("wp "):
                    rest = line[2:].strip()
                    try:
                        wpcli(_shlex.split(rest), instance=name, check=False)
                    except Exception as e:
                        print(f"error: {e}"); return False
                else:
                    # shell in the wp container (sh -c "<line>")
                    compose("exec", "-T", "wp", "sh", "-c", line,
                            instance=name, check=False)
            return True

        label = f"{action}" + (f" {name}" if action != "wp"
                               else f" {name}: {payload.get('args','')}")
        # Serialize against other mutating actions via the lock inside the job.
        def locked():
            with _web_lock:
                return run_op()
        return {"ok": True, "job_id": _start_job(label, locked)}

    # Switch an instance's web server in place. Backgrounded + streamed because
    # it recreates the web tier and may pull the OpenLiteSpeed image (slow).
    if action == "server":
        if not name:
            return {"ok": False, "output": "missing instance"}
        try:
            target = _valid_server(payload.get("server"))
        except SystemExit:
            return {"ok": False, "output": "invalid server (apache|nginx|litespeed)"}
        label = f"server {name} → {target}"

        def do_server():
            with _web_lock:
                cmd_server(load_config(), _types.SimpleNamespace(
                    name=name, server_type=target, resolved_instance=name))
        return {"ok": True, "job_id": _start_job(label, do_server)}

    if action == "create":
        # Per-project model: instances are created by `./sb init` / `./sb ensure`
        # inside a plugin repo (keyed to the project dir), not by name here.
        return {"ok": False, "output":
                "Create an instance by running `./sb init` (or `./sb ensure`) "
                "inside a plugin repo — not from the dashboard."}

    if action == "delete":
        if payload.get("confirm") != name:
            return {"ok": False,
                    "output": "delete requires confirm == instance name"}

        def do_inst():
            with _web_lock:
                cmd_instance(load_config(), _types.SimpleNamespace(
                    action="delete", name=name, yes=True, resolved_instance=name))
        return {"ok": True, "job_id": _start_job(f"Deleting {name}", do_inst)}

    return {"ok": False, "output": f"unknown action '{action}'"}


def _web_css() -> str:
    """Vendored, pre-built Tailwind CSS (config/sandbox-web.css). Inlined into
    the page so the UI is fully self-contained — no CDN, works offline.
    Rebuild after editing classes: scripts/build-web-css.sh."""
    if _WEB_CSS_CACHE[0] is None:
        css_path = ROOT / "config" / "sandbox-web.css"
        try:
            _WEB_CSS_CACHE[0] = css_path.read_text()
        except OSError:
            _WEB_CSS_CACHE[0] = ""   # graceful: unstyled but functional
    return _WEB_CSS_CACHE[0]


def _web_js() -> str:
    """Vendored, pre-built dashboard bundle (config/sandbox-web.js) — compiled
    from the TypeScript source in src/web by Vite. Inlined into the page so the
    UI is fully self-contained (no node/CDN at runtime). Rebuild after editing
    src/web: scripts/build-web-js.sh."""
    if _WEB_JS_CACHE[0] is None:
        js_path = ROOT / "config" / "sandbox-web.js"
        try:
            _WEB_JS_CACHE[0] = js_path.read_text()
        except OSError:
            _WEB_JS_CACHE[0] = "console.error('sandbox-web.js missing — run scripts/build-web-js.sh');"
    return _WEB_JS_CACHE[0]
