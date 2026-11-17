"""Side-by-side runtime releases on a remote (versioned install layout).

Every runtime revision is installed into its own directory,
``$SANDBOX_HOME/releases/<runtime-revision>/``, with its own virtual
environments, and is built and verified completely while the old service keeps
serving and no install lock is held. Activation then only flips the
``current`` symlink (a rename over it) and restarts the service, so the job
admission lock covers seconds, not a venv build.

``sb-src`` stays as a compatibility symlink to ``current``: routines, hosted
helpers and SSH submissions keep running ``sb-src/sb``. Everything below them
resolves its own path (``Path(__file__).resolve()``), so a running job keeps
the release it was admitted with even after ``current`` moves on.

``PROGRAM`` is fixed text the controller sends and the remote ``python3`` runs
directly (stdlib only, like ``pins.PROGRAM``); the installed runtime never
imports it. Actions:

``prepare HOME STAGE RECEIPT RUNTIME_REV SOURCE_REV``
    Verify the staged tree and its receipt, move it to ``releases/<rev>`` and
    build it there (venvs bake their absolute path, so a release is built in
    place and marked complete last). An already complete release is reused.
``activate HOME RUNTIME_REV UNIT_PATH ORIGIN_PATH``
    Point ``current`` at the release and ``previous`` at the old target. On a
    host still in the old layout (a plain ``sb-src`` directory) this is the
    one-time conversion: the old tree becomes ``releases/<old-rev>`` and
    ``sb-src`` becomes the symlink, with no moment where ``sb-src`` is
    missing. Prints the state ``revert`` needs.
``revert HOME STATE_JSON``
    Undo one ``activate`` (failure rollback), including the conversion.
``record HOME RUNTIME_REV UNIT_PATH SOURCE_RECEIPT ORIGIN_PATH``
    Keep the installed unit, source receipt and owner origin record beside
    the release, so a later rollback restores exactly what it ran with.
``rollback-target HOME``
    Print the release ``previous`` points at, when it is complete.
``restore HOME RUNTIME_REV UNIT_PATH SOURCE_RECEIPT ORIGIN_PATH``
    Put a release's recorded unit, source receipt and owner origin back.
``gc HOME``
    Keep ``current``, ``previous`` and the last ``KEEP`` activated releases;
    remove older ones that no live process references.
``list HOME``
    Read-only listing.
"""
from __future__ import annotations

import shlex

KEEP_RELEASES = 3

PROGRAM = r'''
import ctypes, fcntl, json, os, re, shutil, stat, subprocess, sys, time
home = os.path.abspath(sys.argv[1]); action = sys.argv[2]; argv = sys.argv[3:]
REL = os.path.join(home, "releases")
CURRENT = os.path.join(home, "current")
PREVIOUS = os.path.join(home, "previous")
LEGACY = os.path.join(home, "sb-src")
MARKER = ".release.json"
KEEP = 3
NAME = re.compile(r"[0-9a-f]{24}|legacy-[0-9]{10,12}")
uid = os.geteuid()

def fail(code, message):
    sys.stderr.write("sandbox-releases: " + message + "\n")
    raise SystemExit(code)

def owner_dir(path, mode=None):
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        fail(1, "unsafe directory " + os.path.basename(path))
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        fail(1, "unsafe directory mode " + os.path.basename(path))

def owner_file(path, mode):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode:
        fail(1, "unsafe file " + os.path.basename(path))

def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def put(path, text, mode=0o600):
    tmp = path + ".tmp-%d" % os.getpid()
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)

def link(path, target):
    """Point the symlink ``path`` at ``target`` atomically (rename over it)."""
    tmp = path + ".tmp-%d" % os.getpid()
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    os.symlink(target, tmp)
    os.replace(tmp, path)

def exchange(a, b):
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, os.fsencode(a), -100, os.fsencode(b), 2)
    else:
        result = libc.renamex_np(os.fsencode(a), os.fsencode(b), 2)
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), b)

def releases_dir():
    if not os.path.lexists(REL):
        os.mkdir(REL, 0o700)
    owner_dir(REL)
    os.chmod(REL, 0o700)
    return REL

def lock(blocking=True):
    handle = os.open(os.path.join(releases_dir(), ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        os.close(handle)
        return None
    return handle

def marker(name):
    path = os.path.join(REL, name, MARKER)
    try:
        owner_file(path, 0o600)
        with open(path) as handle:
            value = json.load(handle)
    except (OSError, ValueError, SystemExit):
        return None
    if not isinstance(value, dict) or value.get("state") != "complete" \
            or value.get("name") != name:
        return None
    return value

def target_name(path):
    """The release name a symlink in ``home`` points at, else None."""
    try:
        value = os.readlink(path)
    except OSError:
        return None
    parts = value.split("/")
    if len(parts) == 2 and parts[0] == "releases" and NAME.fullmatch(parts[1]):
        return parts[1]
    return None

def unit_revision(unit_path):
    try:
        lines = open(unit_path).read().splitlines()
    except OSError:
        return None
    prefix = "Environment=SANDBOX_REMOTE_MCP_RUNTIME_REVISION="
    found = [line[len(prefix):] for line in lines if line.startswith(prefix)]
    return found[0] if len(found) == 1 and re.fullmatch(r"[0-9a-f]{24}", found[0]) else None

def run(args, cwd):
    subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, check=True)

def prepare(stage_name, receipt, rev, source):
    if not re.fullmatch(r"\.sb-src-stage-[0-9a-f]{12}-[0-9a-f]{32}", stage_name) \
            or not re.fullmatch(r"[0-9a-f]{24}", rev) or not re.fullmatch(r"[0-9a-f]{40}", source):
        fail(2, "invalid prepare request")
    stage = os.path.join(home, stage_name); tree = os.path.join(stage, "tree")
    owner_dir(stage, 0o700); owner_dir(tree, 0o700)
    for relative in ("sb", "scripts/provision_image_stage_helper.py"):
        info = os.lstat(os.path.join(tree, relative))
        if not stat.S_ISREG(info.st_mode):
            fail(1, "staged tree is incomplete")
    owner_file(os.path.join(stage, "receipt.json"), 0o600)
    with open(os.path.join(stage, "receipt.json")) as handle:
        if handle.read() != receipt:
            fail(1, "staged receipt does not match")
    held = lock()
    target = os.path.join(REL, rev)
    state = "reused"
    if marker(rev) is None:
        state = "built"
        if os.path.lexists(target):
            if target_name(CURRENT) == rev or target_name(PREVIOUS) == rev:
                fail(1, "an incomplete release is active")
            shutil.rmtree(target)
        os.rename(tree, target)
        fsync_dir(REL)
        venv = os.path.join(target, ".cli-venv")
        run(["python3", "-m", "venv", venv], target)
        run([os.path.join(venv, "bin", "python"), "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "pyyaml"], target)
    # Content-addressed per source revision: refreshed even on reuse.
    run(["python3", os.path.join(target, "scripts", "provision_image_stage_helper.py"),
         "--sandbox-home", home, "--runtime-revision", source], target)
    if state == "built":
        run(["./sb", "mcp-install"], target)
        run([os.path.join(target, ".cli-venv", "bin", "python"), "-c",
             "from sandbox.core._config import ensure_tools_venv; ensure_tools_venv()"], target)
        for relative in ("sb", "sandbox/hermes/cron-catalog.json"):
            if not os.path.isfile(os.path.join(target, relative)):
                fail(1, "built release is incomplete")
        put(os.path.join(target, MARKER), json.dumps({
            "schema_version": 1, "name": rev, "state": "complete", "runtime_revision": rev,
            "source_revision": source, "receipt": receipt, "built_at": int(time.time())},
            sort_keys=True))
        fsync_dir(target)
    shutil.rmtree(stage, ignore_errors=True)
    os.close(held)
    print(json.dumps({"release": rev, "state": state}, sort_keys=True), flush=True)

def activate(rev, unit_path, origin_path):
    # Exit 3: refused before any change, nothing to revert.
    releases_dir()
    if marker(rev) is None:
        fail(3, "release is not complete")
    state = {"release": rev, "converted": False,
             "had_current": os.path.lexists(CURRENT), "current": None,
             "had_previous": os.path.lexists(PREVIOUS), "previous": None}
    if os.path.lexists(CURRENT):
        state["current"] = os.readlink(CURRENT)
    if os.path.lexists(PREVIOUS):
        state["previous"] = os.readlink(PREVIOUS)
    legacy = os.lstat(LEGACY) if os.path.lexists(LEGACY) else None
    if legacy is not None and stat.S_ISDIR(legacy.st_mode):
        if legacy.st_uid != uid:
            fail(3, "unsafe legacy runtime")
        old = unit_revision(unit_path)
        name = old if old and old != rev and not os.path.lexists(os.path.join(REL, old)) \
            else "legacy-%d" % int(time.time())
        if os.path.lexists(os.path.join(REL, name)):
            fail(3, "legacy release name is taken")
        # current first, so sb-src never dangles: current -> new release,
        # then exchange a symlink with the old directory, then file the
        # directory under releases/.
        link(CURRENT, "releases/" + rev)
        hold = os.path.join(home, ".sb-src-legacy-%d" % os.getpid())
        os.symlink("current", hold)
        exchange(hold, LEGACY)
        os.rename(hold, os.path.join(REL, name))
        put(os.path.join(REL, name, MARKER), json.dumps({
            "schema_version": 1, "name": name, "state": "complete", "runtime_revision": old,
            "legacy": True, "built_at": int(time.time())}, sort_keys=True))
        for source, kept in ((unit_path, ".service-unit"),
                             (os.path.join(home, "runtime", "source-revision.json"), ".source-revision.json"),
                             (origin_path, ".owner-origin.json")):
            if os.path.isfile(source) and not os.path.islink(source):
                shutil.copyfile(source, os.path.join(REL, name, kept))
                os.chmod(os.path.join(REL, name, kept), 0o600)
        link(PREVIOUS, "releases/" + name)
        state.update(converted=True, legacy=name)
    else:
        if legacy is not None and not (stat.S_ISLNK(legacy.st_mode) and os.readlink(LEGACY) == "current"):
            fail(3, "sb-src is neither the old runtime nor the compatibility link")
        old = target_name(CURRENT)
        link(CURRENT, "releases/" + rev)
        if old is not None and old != rev:
            link(PREVIOUS, "releases/" + old)
        if legacy is None:
            os.symlink("current", LEGACY)
            state["created_legacy_link"] = True
    os.utime(os.path.join(REL, rev, MARKER))
    fsync_dir(home)
    print(json.dumps(state, sort_keys=True), flush=True)

def restore_link(path, had, value):
    if had:
        link(path, value)
    elif os.path.lexists(path):
        os.unlink(path)

def revert(text):
    state = json.loads(text)
    if state.get("converted"):
        name = state["legacy"]
        directory = os.path.join(REL, name)
        exchange(directory, LEGACY)
        os.unlink(directory)
        for kept in (MARKER, ".service-unit", ".source-revision.json", ".owner-origin.json"):
            try:
                os.unlink(os.path.join(LEGACY, kept))
            except FileNotFoundError:
                pass
    elif state.get("created_legacy_link") and os.path.islink(LEGACY):
        os.unlink(LEGACY)
    restore_link(CURRENT, state.get("had_current"), state.get("current"))
    restore_link(PREVIOUS, state.get("had_previous"), state.get("previous"))
    fsync_dir(home)

def copy_private(source, target):
    if os.path.isfile(source) and not os.path.islink(source):
        tmp = target + ".tmp-%d" % os.getpid()
        shutil.copyfile(source, tmp); os.chmod(tmp, 0o600); os.replace(tmp, target)
    elif os.path.lexists(target):
        os.unlink(target)

def record(rev, unit_path, receipt_path, origin_path):
    if marker(rev) is None:
        fail(1, "release is not complete")
    for source, kept in ((unit_path, ".service-unit"), (receipt_path, ".source-revision.json"),
                         (origin_path, ".owner-origin.json")):
        copy_private(source, os.path.join(REL, rev, kept))

def restore(rev, unit_path, receipt_path, origin_path):
    if marker(rev) is None or not os.path.isfile(os.path.join(REL, rev, ".service-unit")):
        fail(1, "release has no recorded service unit")
    for target, kept in ((unit_path, ".service-unit"), (receipt_path, ".source-revision.json"),
                         (origin_path, ".owner-origin.json")):
        copy_private(os.path.join(REL, rev, kept), target)

def referenced():
    """Release names a live process runs from (cwd or command line)."""
    prefix = REL + "/"; found = set()
    for pid in os.listdir("/proc") if os.path.isdir("/proc") else ():
        if not pid.isdigit():
            continue
        texts = []
        try:
            texts.append(os.readlink("/proc/%s/cwd" % pid))
        except OSError:
            pass
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as handle:
                texts.extend(handle.read().decode("utf-8", "replace").split("\0"))
        except OSError:
            pass
        for text in texts:
            if text.startswith(prefix):
                found.add(text[len(prefix):].split("/", 1)[0])
    return found

def listing():
    names = []
    if os.path.isdir(REL):
        for name in os.listdir(REL):
            if NAME.fullmatch(name) and not os.path.islink(os.path.join(REL, name)):
                value = marker(name)
                stamp = os.path.getmtime(os.path.join(REL, name, MARKER)) if value else 0
                names.append((stamp, name, value is not None, (value or {}).get("runtime_revision")))
    names.sort(reverse=True)
    return names

def gc():
    held = lock(blocking=False)
    if held is None:
        print(json.dumps({"removed": [], "skipped": "build_in_progress"}), flush=True)
        return
    keep = {target_name(CURRENT), target_name(PREVIOUS)}
    complete = [name for _stamp, name, ok, _rev in listing() if ok]
    keep.update(complete[:KEEP])
    live = referenced()
    removed = []
    for _stamp, name, ok, _rev in listing():
        if name in keep or name in live:
            continue
        shutil.rmtree(os.path.join(REL, name), ignore_errors=True)
        removed.append(name)
    os.close(held)
    print(json.dumps({"removed": sorted(removed), "kept": sorted(n for n in keep if n)}), flush=True)

if action == "prepare":
    prepare(*argv)
elif action == "activate":
    activate(*argv)
elif action == "revert":
    revert(argv[0])
elif action == "record":
    record(*argv)
elif action == "restore":
    restore(*argv)
elif action == "rollback-target":
    name = target_name(PREVIOUS)
    if name is None or marker(name) is None or name == target_name(CURRENT):
        fail(3, "no complete previous release to roll back to")
    print(name, flush=True)
elif action == "gc":
    gc()
elif action == "list":
    print(json.dumps({"layout": "releases" if os.path.islink(LEGACY) else
                      ("legacy" if os.path.isdir(LEGACY) else "absent"),
                      "current": target_name(CURRENT), "previous": target_name(PREVIOUS),
                      "releases": [{"name": n, "complete": ok, "runtime_revision": r}
                                   for _s, n, ok, r in listing()]},
                     sort_keys=True), flush=True)
else:
    raise SystemExit(2)
'''


def shell(action: str, *args: str) -> str:
    """Shell text running one ``PROGRAM`` action against ``$HOME/sandbox``.

    Arguments are quoted; one that starts with ``$`` is a shell variable
    reference and is double-quoted instead, so it expands remotely.
    """
    quoted = [f'"{arg}"' if arg.startswith("$") else shlex.quote(arg) for arg in args]
    return " ".join(["python3", "-c", shlex.quote(PROGRAM), '"$HOME/sandbox"',
                     shlex.quote(action), *quoted])
