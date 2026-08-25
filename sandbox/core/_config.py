from __future__ import annotations
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr


def ensure_pyyaml() -> None:
    """Ensure PyYAML is importable.

    PEP 668 ("externally-managed-environment") prevents pip --user installs
    against system/homebrew Pythons, so we keep the CLI's deps in its own
    venv at .cli-venv/ and re-exec ourselves through it on first run.
    """
    try:
        import yaml  # noqa: F401
        return
    except ImportError:
        pass

    cli_py = CLI_VENV / "bin" / "python"
    if not cli_py.exists():
        # A killed or interrupted first bootstrap can leave the venv directory
        # behind without its interpreter.  Passing that path back to
        # ``python -m venv`` raises ``FileExistsError`` instead of recovering.
        # Only remove a real directory at the generated CLI-venv location;
        # never follow or replace a user-supplied file/symlink.
        if CLI_VENV.is_symlink() or (CLI_VENV.exists() and not CLI_VENV.is_dir()):
            die("CLI venv path exists but is not a directory; remove it and retry")
        if CLI_VENV.is_dir():
            info("Incomplete CLI venv found; recreating .cli-venv/…")
            shutil.rmtree(CLI_VENV)
        info("Creating CLI venv at .cli-venv/ (one-time)…")
        subprocess.check_call([sys.executable, "-m", "venv", str(CLI_VENV)])
        subprocess.check_call(
            [str(CLI_VENV / "bin" / "pip"), "install", "--quiet",
             "--disable-pip-version-check", "pyyaml"]
        )

    # If we're not already running under the CLI venv, re-exec there.
    # Compare sys.prefix (not sys.executable, which resolves to the underlying
    # interpreter binary that the venv symlinks to).
    if Path(sys.prefix).resolve() != CLI_VENV.resolve():
        # Replay the original argv only when this process genuinely is the
        # Sandbox CLI entry point. A foreign caller (unittest discovery, an
        # embedded interpreter, another tool importing this package) cannot be
        # replaced by an argv replay it never expressed: os.execv would swap
        # the whole process for a bogus `sb <foreign argv>` invocation and
        # silently kill the host process.
        try:
            invoked_as_cli = Path(sys.argv[0] or "").resolve() == ENTRY.resolve()
        except OSError:
            invoked_as_cli = False
        if invoked_as_cli:
            os.execv(str(cli_py), [str(cli_py), str(ENTRY), *sys.argv[1:]])
        die("PyYAML is required but unavailable outside the Sandbox CLI venv; "
            "run commands through ./sb (or .cli-venv/bin/python), or install "
            "pyyaml into the current interpreter.")


def expand(value, vars_: dict) -> object:
    """Recursively expand ${var} references using vars_."""
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: str(vars_.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [expand(v, vars_) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, vars_) for k, v in value.items()}
    return value


def load_config() -> dict:
    ensure_pyyaml()
    import yaml
    if not CONFIG.exists():
        die(f"missing {CONFIG} — run from the sandbox/ directory")
    with CONFIG.open() as f:
        cfg = yaml.safe_load(f) or {}
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            local = yaml.safe_load(f) or {}
        cfg = deep_merge(cfg, local)
    vars_ = cfg.get("defaults", {}) or {}
    return expand(cfg, vars_)


def deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _plugins_home(cfg: dict) -> Path:
    """Resolve `defaults.plugins_home` to an absolute path, creating it."""
    defaults = cfg.get("defaults", {}) or {}
    raw = defaults.get("plugins_home", "") or "./plugins"
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _venv_paths_match(venv: Path) -> bool:
    """True if the venv's recorded home/prefix still points inside `venv` — i.e.
    it has not been moved. A relocated venv has a stale absolute path baked into
    pyvenv.cfg and must be recreated, not reused."""
    cfg = venv / "pyvenv.cfg"
    if not cfg.exists():
        return False
    try:
        text = cfg.read_text()
    except OSError:
        return False
    target = str(venv.resolve())
    # pyvenv.cfg's `home` and `executable` values identify the *base
    # interpreter*, not the venv directory.  In particular, a valid Python
    # 3.14 venv normally has an `executable = /opt/homebrew/.../python3.14`
    # line which can never contain `target`.  Treating that line as a path
    # attestation made every `visit` invocation delete and rebuild a healthy
    # venv after a workspace relocation.
    vpy = venv / "bin" / "python"
    if not vpy.exists():
        return False
    for line in text.splitlines():
        low = line.strip().lower()
        if not low.startswith("command") or "=" not in line:
            continue
        # Newer Python versions record the command used to create the venv.
        # When it includes an absolute destination, that is useful relocation
        # evidence; when it is absent (older Python), the working interpreter
        # above is the only portable signal and we keep the venv.
        try:
            tokens = shlex.split(line.split("=", 1)[1].strip())
        except ValueError:
            continue
        try:
            venv_at = tokens.index("venv")
        except ValueError:
            continue
        destination = next(
            (item for item in reversed(tokens[venv_at + 1:])
             if not item.startswith("-")),
            None,
        )
        if not destination or not Path(destination).is_absolute():
            continue
        try:
            if Path(destination).resolve() != Path(target).resolve():
                return False
        except OSError:
            return False
    return True


def ensure_tools_venv() -> Path:
    """Build the headless-browser venv on first use and return its python path.

    Lives under runtime/.venv-tools/ so it sits next to other auto-managed
    state and is wiped by `./sb clean`. The Chromium binary that Playwright
    downloads lands in the playwright cache under the venv.
    """
    py = TOOLS_VENV / "bin" / "python"
    req = TOOLS_DIR / "visit" / "requirements.txt"
    stamp = TOOLS_VENV / ".installed"

    # A venv bakes its absolute path into bin/python (shebangs) and pyvenv.cfg.
    # If it was moved (e.g. relocating the base, spec 009), those point at the
    # old location and `python` is broken — recreate rather than move (FR-009).
    cfg = TOOLS_VENV / "pyvenv.cfg"
    if TOOLS_VENV.exists() and not _venv_paths_match(TOOLS_VENV):
        info("Tools venv path changed (base moved); recreating runtime/.venv-tools/…")
        shutil.rmtree(TOOLS_VENV, ignore_errors=True)

    if py.exists() and stamp.exists() and stamp.read_text().strip() == req.read_text().strip():
        return py

    if not py.exists():
        info("Creating tools venv at runtime/.venv-tools/ (one-time)…")
        TOOLS_VENV.parent.mkdir(parents=True, exist_ok=True)
        _make_venv(find_modern_python(), TOOLS_VENV)

    # `ensurepip` is allowed to expose only versioned entry points (`pip3`,
    # `pip3.14`) on some Homebrew Python builds.  Calling the module through
    # the venv interpreter works with all of those layouts and avoids a
    # false FileNotFoundError for the unversioned `bin/pip` shim.
    pip = [str(py), "-m", "pip"]
    pip_check = subprocess.run(
        [*pip, "--version"], capture_output=True, text=True,
    )
    if pip_check.returncode != 0:
        subprocess.check_call([str(py), "-m", "ensurepip", "--upgrade"])
    info("Installing Playwright (one-time)…")
    subprocess.check_call([*pip, "install", "--quiet",
                           "--disable-pip-version-check", "-r", str(req)])
    info("Downloading headless Chromium (one-time, ~150 MB)…")
    # Pin to chromium only — we don't need firefox/webkit and don't want
    # to wait for 3x the download on first run.
    subprocess.check_call([str(py), "-m", "playwright", "install", "chromium"])
    stamp.write_text(req.read_text())
    ok("Tools venv ready.")
    return py


def find_modern_python() -> str:
    """Pick a Python >= 3.10 that can actually build a working venv. We've seen
    a Homebrew python3.13 with a broken pyexpat (libexpat symbol mismatch) that
    passes a version check but fails ensurepip — so each candidate is validated
    by importing the stdlib modules venv/ensurepip need, not just its version.
    Prefers the highest usable version; includes python3/3.14 in the list."""
    candidates = [
        "python3.14", "python3.13", "python3.12", "python3.11", "python3.10",
        "python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3",
    ]
    fallback = None
    for c in candidates:
        if not shutil.which(c) and not Path(c).exists():
            continue
        try:
            v = subprocess.check_output(
                [c, "-c", "import sys;print(sys.version_info[:2])"], text=True
            ).strip()
            if eval(v) < (3, 10):
                continue
            fallback = fallback or c
            # Validate the interpreter is actually usable for a venv: the
            # modules ensurepip pulls in (pyexpat via xml, ssl, ensurepip) must
            # import cleanly. A broken pyexpat here is what fails `-m venv`.
            chk = subprocess.run(
                [c, "-c", "import ensurepip, ssl, pyexpat, xml.parsers.expat"],
                capture_output=True, text=True)
            if chk.returncode == 0:
                return c
        except Exception:
            continue
    # No fully-validated interpreter — return the best version-only match (the
    # venv builder has its own --without-pip + get-pip fallback) or python3.
    return fallback or "python3"


def _make_venv(py: str, path: Path) -> None:
    """Create a venv robustly. Some interpreters (e.g. Homebrew python3.13) fail
    `python -m venv` at the internal ensurepip step. Fall back to building the
    venv WITHOUT pip, then bootstrap pip via ensurepip → get-pip."""
    r = subprocess.run([py, "-m", "venv", str(path)],
                       capture_output=True, text=True)
    vpy = path / "bin" / "python"
    if r.returncode == 0 and vpy.exists():
        return
    # Fallback: pip-less venv + bootstrap pip.
    info("venv+pip failed; retrying without pip, then bootstrapping pip…")
    shutil.rmtree(path, ignore_errors=True)
    run([py, "-m", "venv", "--without-pip", str(path)])
    # 1) try ensurepip inside the venv
    if subprocess.run([str(vpy), "-m", "ensurepip", "--upgrade"],
                      capture_output=True, text=True).returncode == 0:
        return
    # 2) last resort: get-pip.py
    import urllib.request, tempfile
    info("ensurepip unavailable; fetching get-pip.py…")
    with tempfile.NamedTemporaryFile("wb", suffix=".py", delete=False) as f:
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", f.name)
        gp = f.name
    run([str(vpy), gp])
    Path(gp).unlink(missing_ok=True)


def _local_yaml() -> dict:
    ensure_pyyaml()
    import yaml
    if CONFIG_LOCAL.exists():
        with CONFIG_LOCAL.open() as f:
            return yaml.safe_load(f) or {}
    return {}


def _write_local_yaml(local: dict) -> None:
    ensure_pyyaml()
    import yaml
    with CONFIG_LOCAL.open("w") as f:
        yaml.safe_dump(local, f, default_flow_style=False, sort_keys=False)


def _write_env_local(values: dict) -> None:
    """Write/merge KEY=VAL pairs into .env.local. Existing keys are replaced;
    others preserved. Empty values are skipped."""
    existing: dict[str, str] = {}
    if SECRETS_ENV.exists():
        for ln in SECRETS_ENV.read_text().splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                existing[k.strip()] = v
    for k, v in values.items():
        if v:
            existing[k] = v
    lines = ["# Personal secrets for the sandbox — gitignored, never commit.",
             "# Source from your shell or let skills read directly.", ""]
    for k, v in existing.items():
        lines.append(f"{k}={v}")
    SECRETS_ENV.write_text("\n".join(lines) + "\n")
    try:
        SECRETS_ENV.chmod(0o600)
    except OSError:
        pass


def _refresh_env_local() -> None:
    """Mirror current sandbox.local.yml secrets into .env.local."""
    local = _local_yaml()
    fb = local.get("fluentboards", {}) or {}
    _write_env_local({
        "GITHUB_ORG": (local.get("defaults", {}) or {}).get("github_org", ""),
        "FLUENTBOARDS_URL": fb.get("url", ""),
        "FLUENTBOARDS_EMAIL": fb.get("email", ""),
        "FLUENTBOARDS_APP_PASSWORD": fb.get("app_password", ""),
    })
