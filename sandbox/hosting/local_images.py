"""Locally built hosted images: receipt, observation, and transfer mechanisms.

A hosted environment with ``compose.build: false`` deploys onto images that
already exist on the remote Docker engine. This module owns the mechanism that
lets an operator produce those images on a faster local engine instead:

* ``build-local`` renders the same Compose project the remote apply renders
  (same files, project name, ``--env-file`` contents, derived source revision)
  from a clean checkout of one exact commit, builds every declared build image
  and verifies non-built images are digest-pinned,
  service image for one target platform, saves each image to a local archive,
  and writes a closed receipt;
* ``push-local`` streams each archive whose image the remote does not already
  hold through the registered SSH transport into ``docker load``, then proves
  every receipt reference resolves to the receipt identity and records a
  remote-side receipt;
* ``host apply --local-images`` re-proves the same identities against the
  remote Compose model before any Compose mutation.

One read-only probe program runs both locally and on the remote, so the build
definition digest is computed by identical code on both sides. Comparison is
done by the controller. A built image must retain the exact local Docker image
ID, platform, and layer diff-ID digest after loading; archive manifest and
configuration digests are retained as independent evidence, not aliases.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from sandbox.hosting.names import TARGET_PART_RE, COMPOSE_PROJECT_RE

RECEIPT_SCHEMA = "sandbox.host.local-images.v1"
REMOTE_RECEIPT_SCHEMA = "sandbox.host.local-images.remote.v1"
REMOTE_RECEIPT_NAME = "local-images.json"
RECEIPT_FILE_NAME = "receipt.json"
SUPPORTED_PLATFORMS = ("linux/amd64",)
DEFAULT_PLATFORM = "linux/amd64"
MAX_RECEIPT_BYTES = 256 * 1024
MAX_CONFIG_BYTES = 1_048_576
MAX_SERVICES = 64
PROBE_DEADLINE_SECONDS = 90
DEFAULT_TRANSFER_TIMEOUT_SECONDS = 3600
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SERVICE_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}")
_ARTIFACT_RE = re.compile(r"image-[0-9]{1,3}")
_ROLES = ("primary", "background", "init")
# built: built locally; shared: built by another declared service; pinned: an
# already-present digest-pinned registry reference (no archive is made).
_SOURCES = ("built", "shared", "pinned")
# The subprocess environment for local Compose. Compose interpolation prefers
# process variables over ``--env-file`` values; the remote apply runs Compose in
# a minimal SSH session, so the local build must not inherit an operator's
# shell variables. Read these keys one at a time; never enumerate os.environ.
_LOCAL_ENVIRONMENT_KEYS = ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
                           "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY")


class LocalImageError(RuntimeError):
    """A closed, non-secret refusal with a stable code."""

    def __init__(self, code: str, message: str, *, detail: object = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = detail


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _json_loads_closed(value: str | bytes):
    def closed(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=closed)


def declared_services(validated: dict) -> dict[str, str]:
    """Return every declared service with its manifest role, in declared order."""
    compose = validated["compose"]
    services = {compose["service"]: "primary"}
    for name in compose.get("background_services", []):
        services[name] = "background"
    for name in compose.get("init_services", []):
        services[name] = "init"
    return services


def is_pinned(ref: str) -> bool:
    return "@sha256:" in ref


def pinned_refs(receipt: dict) -> list[str]:
    return sorted({service["image"] for service in receipt["services"].values()
                   if service["source"] == "pinned"})


def default_output_directory(runtime_dir: Path, validated: dict, commit: str) -> Path:
    return (Path(runtime_dir) / "hosting" / "local-images" / validated["project"]
            / validated["environment"] / commit)


def local_environment(extra: dict[str, str]) -> dict[str, str]:
    parent_path = os.environ.get("PATH") or os.defpath
    executable_directories = []
    for executable in ("docker", "git", "python3"):
        resolved = shutil.which(executable, path=parent_path)
        if resolved:
            for directory in (str(Path(resolved).parent),
                              str(Path(resolved).resolve().parent)):
                if directory not in executable_directories:
                    executable_directories.append(directory)
    for directory in os.defpath.split(os.pathsep):
        if directory and directory not in executable_directories:
            executable_directories.append(directory)
    environment = {"PATH": os.pathsep.join(executable_directories),
                   "LANG": "C", "LC_ALL": "C"}
    home = os.environ.get("HOME")
    if home:
        environment["HOME"] = home
    for key in _LOCAL_ENVIRONMENT_KEYS:
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment.update(extra)
    return environment


# --------------------------------------------------------------------------
# Shared read-only probe. It runs unchanged on the controller and the remote.
# --------------------------------------------------------------------------

_PROBE_PROGRAM = r'''
import base64,hashlib,json,os,selectors,signal,subprocess,sys,time
p=json.loads(base64.b64decode(sys.argv[1]).decode())
end=time.monotonic()+p["deadline"]
KEYS=("context","dockerfile","dockerfile_inline","args","target","additional_contexts","secrets","ssh","network","extra_hosts","labels","shm_size","privileged","entitlements")
secret_names=set(p.get("symbolic_secrets") or [])
secret_values=sorted(((name,os.environ.get(name)) for name in secret_names if os.environ.get(name)),key=lambda item:len(item[1]),reverse=True)
roots=sorted([(label,path.rstrip("/")) for label,path in p.get("roots",[]) if path],key=lambda item:len(item[1]),reverse=True)
def left():
    return max(0.1,end-time.monotonic())
def digest(value):
    return "sha256:"+hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def norm(value):
    if isinstance(value,str):
        for name,secret in secret_values:
            value=value.replace(secret,"${SECRET:"+name+"}")
        for label,root in roots:
            if value==root or value.startswith(root+"/"):
                return "${"+label+"}"+value[len(root):]
        return value
    if isinstance(value,dict):
        return {str(k):norm(v) for k,v in value.items() if v not in (None,{},[],"")}
    if isinstance(value,list):
        return [norm(v) for v in value]
    return value
def exposed_secret(value):
    if isinstance(value,str):
        return any(secret in value for _name,secret in secret_values)
    if isinstance(value,dict):
        return any(exposed_secret(k) or exposed_secret(v) for k,v in value.items())
    if isinstance(value,list):
        return any(exposed_secret(v) for v in value)
    return False
def build_identity(service):
    image=service.get("image")
    image=image.strip() if isinstance(image,str) and image.strip() else None
    build=service.get("build")
    if isinstance(build,dict):
        selected={k:build[k] for k in KEYS if k in build}
        args=selected.get("args")
        if isinstance(args,dict):
            if any(k in secret_names for k in args): return image,None,True
        elif isinstance(args,list):
            for item in args:
                key=item.split("=",1)[0] if isinstance(item,str) else None
                if key in secret_names: return image,None,True
        if exposed_secret(image) or any(exposed_secret(v) for k,v in selected.items() if k!="secrets"):
            return image,None,True
        if not selected.get("dockerfile") and not selected.get("dockerfile_inline"):
            selected["dockerfile"]="Dockerfile"
        build=norm(selected)
    else:
        build=None
    return image,build,False
def read_config(command,environment=None):
    q=subprocess.Popen(command,shell=True,env=environment,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
    selector=selectors.DefaultSelector();selector.register(q.stdout,selectors.EVENT_READ)
    raw=bytearray();state=None
    while True:
        remaining=end-time.monotonic()
        if remaining<=0:
            state="timeout";break
        if not selector.select(remaining):
            state="timeout";break
        chunk=os.read(q.stdout.fileno(),65536)
        if not chunk:break
        raw.extend(chunk)
        if len(raw)>p["max_config_bytes"]:
            state="too_large";break
    selector.close()
    if state:
        try:os.killpg(q.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    q.wait()
    if state:return None,state
    if q.returncode:return None,"unavailable"
    try:document=json.loads(bytes(raw).decode("utf-8"))
    except Exception:return None,"invalid"
    services=document.get("services") if isinstance(document,dict) else None
    if not isinstance(services,dict):return None,"invalid"
    return services,None
def run(argv,limit=1048576):
    try:q=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=min(30,left()),check=False)
    except (OSError,subprocess.SubprocessError):return None
    if q.returncode or len(q.stdout)>limit:return None
    return q.stdout.decode("utf-8","replace")
out={"schema":1,"engine":None,"services":{},"images":{},"config_error":None,"remote_receipt":None}
if p.get("engine"):
    value=run(["docker","version","--format","{{.Server.Os}}/{{.Server.Arch}}"])
    out["engine"]=value.strip() if value else None
if p.get("config_command"):
    services,error=read_config(p["config_command"])
    # Config rendering needs no daemon. Refuse image interpolation that depends
    # on controller-only Docker client selection, including values in env files.
    client_keys=("DOCKER_HOST","DOCKER_CONTEXT","DOCKER_CONFIG","DOCKER_CERT_PATH","DOCKER_TLS_VERIFY")
    if services is not None and any(os.environ.get(key) for key in client_keys):
        clean={key:value for key,value in os.environ.items() if key not in client_keys}
        portable,portable_error=read_config(p["config_command"],clean)
        if portable_error or any((services.get(name) or {}).get("image")!=(portable.get(name) or {}).get("image") for name in p.get("services",[])):
            error="docker_client_interpolation"
    out["config_error"]=error
    for name in p.get("services",[]):
        service=services.get(name) if services else None
        if not isinstance(service,dict):
            out["services"][name]={"present":False}
            continue
        image,build,secret_exposed=build_identity(service)
        if secret_exposed:
            out["config_error"]="secret_exposed";break
        out["services"][name]={"present":True,"image":image,"pull_policy":service.get("pull_policy"),"build":build is not None,"build_digest":digest({"image":image,"build":build})}
for ref in p.get("refs",[]):
    value=run(["docker","image","inspect","--format","{{json .}}",ref])
    try:document=json.loads(value) if value else None
    except ValueError:document=None
    if not isinstance(document,dict):
        out["images"][ref]={"present":False}
        continue
    layers=(document.get("RootFS") or {}).get("Layers")
    platform="%s/%s"%(document.get("Os") or "",document.get("Architecture") or "")
    out["images"][ref]={"present":True,"id":document.get("Id"),"platform":platform,"layers_digest":digest(layers if isinstance(layers,list) else None),"repo_digests":sorted(document.get("RepoDigests") or [])}
path=p.get("receipt_path")
if path:
    try:
        with open(path,"rb") as handle:
            data=handle.read(p["max_receipt_bytes"]+1)
        if len(data)>p["max_receipt_bytes"]:
            out["remote_receipt"]={"state":"too_large"}
        else:
            document=json.loads(data.decode("utf-8"))
            out["remote_receipt"]={"state":"present","document":document} if isinstance(document,dict) else {"state":"invalid"}
    except FileNotFoundError:
        out["remote_receipt"]={"state":"absent"}
    except Exception:
        out["remote_receipt"]={"state":"invalid"}
print(json.dumps(out,sort_keys=True,separators=(",",":")))
'''


def probe_argv(*, config_command: str | None = None, services=(), refs=(),
               roots: dict[str, str] | None = None, engine: bool = False,
               receipt_path: str | None = None, symbolic_secrets=()) -> list[str]:
    """Return the argv of one bounded read-only probe (shell-joinable)."""
    payload = {
        "deadline": PROBE_DEADLINE_SECONDS,
        "max_config_bytes": MAX_CONFIG_BYTES,
        "max_receipt_bytes": MAX_RECEIPT_BYTES,
        "config_command": config_command,
        "services": list(services),
        "refs": list(refs),
        # Absolute checkout/runtime paths differ between hosts; the build
        # identity replaces them with these stable placeholder labels.
        "roots": sorted([label, str(path)] for label, path in (roots or {}).items()),
        "engine": bool(engine),
        "receipt_path": receipt_path,
        "symbolic_secrets": sorted(set(symbolic_secrets)),
    }
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    return ["python3", "-c", _PROBE_PROGRAM, encoded]


def parse_probe_output(text: str) -> dict:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    try:
        document = _json_loads_closed(lines[-1]) if lines else None
    except ValueError:
        document = None
    if not isinstance(document, dict) or document.get("schema") != 1:
        raise LocalImageError("local_images_observation_unavailable",
                              "image observation returned no complete result")
    return document


# ------------------------------------------------------------------
# Archives and receipts
# ------------------------------------------------------------------

def archive_identity(path: Path, refs: list[str]) -> dict:
    """Read the content identity Docker wrote into one saved archive."""
    try:
        with tarfile.open(path, "r:") as archive:
            members = {member.name: member for member in archive.getmembers()
                       if member.isfile() and member.name in {"manifest.json", "index.json"}}
            def load(name):
                member = members.get(name)
                if member is None or member.size > MAX_RECEIPT_BYTES:
                    return None
                handle = archive.extractfile(member)
                return json.loads(handle.read().decode("utf-8")) if handle else None
            manifest = load("manifest.json")
            index = load("index.json")
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise LocalImageError("local_images_archive_invalid",
                              f"saved image archive is unreadable ({type(exc).__name__})") from None
    if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
        raise LocalImageError("local_images_archive_invalid",
                              "saved image archive must hold exactly one image")
    config = str(manifest[0].get("Config") or "")
    match = re.fullmatch(r"(?:blobs/sha256/|)([0-9a-f]{64})(?:\.json)?", config)
    if match is None:
        raise LocalImageError("local_images_archive_invalid",
                              "saved image archive has no content-addressed config")
    tags = manifest[0].get("RepoTags") or []
    normalized_tags = {_normalize_ref(tag) for tag in tags if isinstance(tag, str)}
    missing = [ref for ref in refs if _normalize_ref(ref) not in normalized_tags]
    if missing:
        raise LocalImageError("local_images_archive_invalid",
                              "saved image archive does not carry every receipt reference")
    manifest_digest = None
    if isinstance(index, dict):
        digests = {item.get("digest") for item in index.get("manifests") or []
                   if isinstance(item, dict)}
        if len(digests) == 1:
            candidate = next(iter(digests))
            if isinstance(candidate, str) and _DIGEST_RE.fullmatch(candidate):
                manifest_digest = candidate
    return {"config_digest": "sha256:" + match.group(1), "manifest_digest": manifest_digest}


def _normalize_ref(ref: str) -> str:
    """Docker's short name for a reference: drop the implicit docker.io/library."""
    value = ref.strip()
    for prefix in ("docker.io/library/", "docker.io/", "index.docker.io/library/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if "@" not in value and ":" not in value.rsplit("/", 1)[-1]:
        value += ":latest"
    return value


def _canonical_pinned_ref(ref: str) -> str | None:
    """Normalize a digest reference while discarding an optional tag."""
    if not isinstance(ref, str) or "@" not in ref:
        return None
    name, digest = ref.rsplit("@", 1)
    if _DIGEST_RE.fullmatch(digest) is None:
        return None
    normalized = _normalize_ref(name)
    tail = normalized.rsplit("/", 1)[-1]
    if ":" in tail:
        normalized = normalized[:-(len(tail))] + tail.rsplit(":", 1)[0]
    return normalized + "@" + digest


def file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return "sha256:" + digest.hexdigest(), size


def seal_receipt(body: dict) -> dict:
    body = {key: value for key, value in body.items() if key != "receipt_digest"}
    return {**body, "receipt_digest": canonical_digest(body)}


def accepted_ids(artifact: dict) -> set[str]:
    value = artifact.get("image_id")
    return {value} if isinstance(value, str) and _DIGEST_RE.fullmatch(value) else set()


def validate_receipt(document: object) -> dict:
    """Validate one closed receipt and its self digest; return it unchanged."""
    def fail(reason: str):
        raise LocalImageError("local_images_receipt_invalid", reason)
    if not isinstance(document, dict) or document.get("schema") != RECEIPT_SCHEMA:
        fail("receipt schema is not " + RECEIPT_SCHEMA)
    expected = {"schema", "project", "environment", "compose_project", "commit",
                "source_revision", "source_root_relative", "platform", "built_at",
                "builder", "build_config_digest", "services", "artifacts",
                "receipt_digest"}
    if set(document) != expected:
        fail("receipt fields are not the closed v1 set")
    if canonical_digest({k: v for k, v in document.items() if k != "receipt_digest"}) \
            != document["receipt_digest"]:
        fail("receipt digest does not match its contents")
    for key in ("commit", "source_revision"):
        if not isinstance(document[key], str) or not _COMMIT_RE.fullmatch(document[key]):
            fail(f"{key} must be a lowercase 40-hex commit")
    if document["platform"] not in SUPPORTED_PLATFORMS:
        fail("platform is not supported")
    for key in ("project", "environment", "compose_project", "source_root_relative", "built_at"):
        if not isinstance(document[key], str) or not document[key]:
            fail(f"{key} must be a non-empty string")
    for key in ("project", "environment"):
        if TARGET_PART_RE.fullmatch(document[key]) is None:
            fail(f"{key} must use lowercase letters, numbers, and hyphens")
    if COMPOSE_PROJECT_RE.fullmatch(document["compose_project"]) is None \
            or document["compose_project"] != (
                f"sandbox-host-{document['project']}-{document['environment']}"):
        fail("compose_project must match the hosted target namespace")
    if not isinstance(document["build_config_digest"], str) \
            or not _DIGEST_RE.fullmatch(document["build_config_digest"]):
        fail("build_config_digest is invalid")
    builder = document["builder"]
    if not isinstance(builder, dict) or set(builder) != {
            "engine_platform", "engine_version", "compose_version"}:
        fail("builder fields are not the closed v1 set")
    if any(value is not None and (
            not isinstance(value, str) or not value or len(value) > 120)
            for value in builder.values()):
        fail("builder values must be bounded strings or null")
    artifacts = document["artifacts"]
    if not isinstance(artifacts, dict) or len(artifacts) > MAX_SERVICES:
        fail("artifacts must be a bounded mapping")
    for key, artifact in artifacts.items():
        if not _ARTIFACT_RE.fullmatch(str(key)) or not isinstance(artifact, dict):
            fail("artifact keys must be image-N mappings")
        if set(artifact) != {"refs", "image_id", "builder_image_id",
                             "manifest_digest", "config_digest",
                             "layers_digest", "platform", "archive", "archive_sha256",
                             "archive_bytes"}:
            fail("artifact fields are not the closed v1 set")
        refs = artifact["refs"]
        if not isinstance(refs, list) or not refs or not all(
                isinstance(ref, str) and ref and len(ref) <= 512 for ref in refs):
            fail("artifact refs must be a non-empty list of image references")
        for field in ("image_id", "builder_image_id", "config_digest",
                      "layers_digest", "archive_sha256"):
            if not isinstance(artifact[field], str) or not _DIGEST_RE.fullmatch(artifact[field]):
                fail(f"artifact {field} must be a sha256 digest")
        if artifact["image_id"] != artifact["config_digest"]:
            fail("artifact image_id must equal its portable archive config_digest")
        if artifact["manifest_digest"] is not None and (
                not isinstance(artifact["manifest_digest"], str)
                or not _DIGEST_RE.fullmatch(artifact["manifest_digest"])):
            fail("artifact manifest_digest must be a sha256 digest or null")
        if artifact["platform"] != document["platform"]:
            fail("artifact platform differs from the receipt platform")
        if artifact["archive"] != f"{key}.tar":
            fail("artifact archive name is not its closed key")
        size = artifact["archive_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            fail("artifact archive_bytes must be a positive integer")
    services = document["services"]
    if not isinstance(services, dict) or not services or len(services) > MAX_SERVICES:
        fail("services must be a non-empty bounded mapping")
    primaries = 0
    for name, service in services.items():
        if not _SERVICE_RE.fullmatch(str(name)) or not isinstance(service, dict):
            fail("service names must be Compose service names")
        if set(service) != {"role", "image", "build_digest", "source", "artifact"}:
            fail("service fields are not the closed v1 set")
        if service["role"] not in _ROLES or service["source"] not in _SOURCES:
            fail("service role or source is invalid")
        primaries += service["role"] == "primary"
        if not isinstance(service["build_digest"], str) \
                or not _DIGEST_RE.fullmatch(service["build_digest"]):
            fail("service build_digest is invalid")
        if service["source"] == "pinned":
            if service["artifact"] is not None or not isinstance(service["image"], str) \
                    or not is_pinned(service["image"]):
                fail("a pinned service must carry a digest reference and no artifact")
            continue
        artifact = artifacts.get(service["artifact"])
        if artifact is None or service["image"] not in artifact["refs"]:
            fail("service image is not carried by its artifact")
    if primaries != 1:
        fail("receipt must declare exactly one primary service")
    if canonical_digest({name: service["build_digest"] for name, service in services.items()}) \
            != document["build_config_digest"]:
        fail("build_config_digest does not match the service build digests")
    return document


def load_receipt(path: str | Path) -> tuple[dict, Path]:
    selected = Path(path).expanduser()
    try:
        info = selected.lstat()
        if selected.is_symlink() or not stat.S_ISREG(info.st_mode) \
                or info.st_uid != os.geteuid() or info.st_nlink != 1 \
                or stat.S_IMODE(info.st_mode) & 0o077:
            raise LocalImageError("local_images_receipt_unavailable",
                                  "receipt file is not owner-only regular content")
        receipt_path = selected.resolve(strict=True)
        raw = receipt_path.read_bytes()
    except OSError as exc:
        raise LocalImageError("local_images_receipt_unavailable",
                              f"receipt file is unreadable ({type(exc).__name__})") from None
    if len(raw) > MAX_RECEIPT_BYTES:
        raise LocalImageError("local_images_receipt_invalid", "receipt file is too large")
    try:
        document = _json_loads_closed(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LocalImageError("local_images_receipt_invalid", "receipt is not JSON") from None
    return validate_receipt(document), receipt_path


def require_receipt_target(validated: dict, receipt: dict, compose_project: str) -> None:
    """Refuse a receipt for another target, a build-enabled manifest, or service set."""
    if validated["compose"].get("build", True) is not False:
        raise LocalImageError(
            "local_images_build_enabled",
            "locally built images require compose.build: false for this environment")
    for key, expected in (("project", validated["project"]),
                          ("environment", validated["environment"]),
                          ("compose_project", compose_project)):
        if receipt[key] != expected:
            raise LocalImageError(
                "local_images_target_mismatch",
                f"receipt {key} {receipt[key]!r} does not match {expected!r}")
    declared = declared_services(validated)
    observed = {name: service["role"] for name, service in receipt["services"].items()}
    if observed != declared:
        raise LocalImageError(
            "local_images_service_set_mismatch",
            "receipt services/roles differ from the manifest's declared services",
            detail={"declared": sorted(declared), "receipt": sorted(observed)})


def remote_receipt_document(receipt: dict, verified: dict[str, dict], *, verified_at: str) -> dict:
    return {
        "schema": REMOTE_RECEIPT_SCHEMA,
        "receipt_digest": receipt["receipt_digest"],
        "project": receipt["project"],
        "environment": receipt["environment"],
        "commit": receipt["commit"],
        "source_revision": receipt["source_revision"],
        "platform": receipt["platform"],
        "verified_at": verified_at,
        "local_receipt": receipt,
        "images": {ref: {"id": item["id"], "artifact": item["artifact"]}
                   for ref, item in sorted(verified.items())},
    }


# ------------------------------------------------------------------
# Comparison (controller side; the probe only observes)
# ------------------------------------------------------------------

def compare_images(receipt: dict, observation: dict, *, artifacts=None) -> tuple[list[dict], dict]:
    """Return (mismatches, verified-by-ref) for receipt artifact references."""
    mismatches: list[dict] = []
    verified: dict[str, dict] = {}
    observed = observation.get("images") or {}
    for key, artifact in sorted(receipt["artifacts"].items()):
        if artifacts is not None and key not in artifacts:
            continue
        allowed = accepted_ids(artifact)
        for ref in artifact["refs"]:
            item = observed.get(ref) or {}
            if item.get("present") is not True:
                mismatches.append({"code": "image_missing", "artifact": key, "ref": ref})
                continue
            if item.get("platform") != artifact["platform"]:
                mismatches.append({"code": "image_platform_mismatch", "artifact": key,
                                   "ref": ref, "expected": artifact["platform"],
                                   "observed": item.get("platform")})
                continue
            if item.get("id") not in allowed:
                mismatches.append({"code": "image_id_mismatch", "artifact": key, "ref": ref,
                                   "expected": sorted(allowed), "observed": item.get("id")})
                continue
            if item.get("layers_digest") != artifact["layers_digest"]:
                mismatches.append({"code": "image_layers_mismatch", "artifact": key,
                                   "ref": ref})
                continue
            verified[ref] = {"id": item["id"], "artifact": key}
    if artifacts is None:
        for ref in pinned_refs(receipt):
            item = observed.get(ref) or {}
            if item.get("present") is not True:
                mismatches.append({"code": "image_missing", "artifact": None, "ref": ref})
            elif item.get("platform") != receipt["platform"]:
                mismatches.append({"code": "image_platform_mismatch", "artifact": None,
                                   "ref": ref, "expected": receipt["platform"],
                                   "observed": item.get("platform")})
            elif _canonical_pinned_ref(ref) not in {
                    _canonical_pinned_ref(value)
                    for value in (item.get("repo_digests") or [])}:
                mismatches.append({"code": "image_digest_mismatch", "artifact": None,
                                   "ref": ref})
            else:
                verified[ref] = {"id": item.get("id"), "artifact": None}
    return mismatches, verified


def compare_engine(receipt: dict, observation: dict) -> list[dict]:
    engine = observation.get("engine")
    if engine != receipt["platform"]:
        return [{"code": "engine_platform_mismatch", "expected": receipt["platform"],
                 "observed": engine}]
    return []


def compare_compose_model(receipt: dict, observation: dict) -> list[dict]:
    """Prove the deployed Compose model resolves to the receipt refs and builds."""
    if observation.get("config_error"):
        return [{"code": "compose_config_" + str(observation["config_error"])}]
    mismatches = []
    observed = observation.get("services") or {}
    for name, service in sorted(receipt["services"].items()):
        item = observed.get(name) or {}
        if item.get("present") is not True:
            mismatches.append({"code": "service_missing", "service": name})
            continue
        if item.get("image") != service["image"]:
            mismatches.append({"code": "service_image_mismatch", "service": name,
                               "expected": service["image"], "observed": item.get("image")})
            continue
        if item.get("pull_policy") == "build":
            mismatches.append({"code": "service_pull_policy_build", "service": name})
            continue
        if item.get("build_digest") != service["build_digest"]:
            mismatches.append({"code": "service_build_definition_mismatch", "service": name})
    return mismatches


def observe_local_model(*, config_command: str, services, roots, environment,
                        symbolic_secrets=()) -> dict:
    return parse_probe_output(_probe_local(
        probe_argv(config_command=config_command, services=services, roots=roots,
                   symbolic_secrets=symbolic_secrets),
        environment))


def compare_remote_receipt(receipt: dict, observation: dict) -> list[dict]:
    state = observation.get("remote_receipt") or {}
    if state.get("state") != "present":
        return [{"code": "remote_receipt_" + str(state.get("state") or "unavailable")}]
    document = state.get("document") or {}
    if document.get("schema") != REMOTE_RECEIPT_SCHEMA \
            or document.get("receipt_digest") != receipt["receipt_digest"] \
            or document.get("commit") != receipt["commit"]:
        return [{"code": "remote_receipt_mismatch",
                 "expected": receipt["receipt_digest"],
                 "observed": document.get("receipt_digest")}]
    return []


def mismatch_summary(mismatches: list[dict], limit: int = 6) -> str:
    parts = []
    for item in mismatches[:limit]:
        subject = item.get("service") or item.get("ref") or ""
        parts.append(item["code"] + (f" ({subject})" if subject else ""))
    if len(mismatches) > limit:
        parts.append(f"+{len(mismatches) - limit} more")
    return ", ".join(parts)


# ------------------------------------------------------------------
# Local build
# ------------------------------------------------------------------

def _run_local(command: str, *, environment: dict, timeout: int, progress=None,
               cwd: str | None = None) -> tuple[int, str]:
    """Run one local shell command; stream lines to progress, keep a bounded tail."""
    process = subprocess.Popen(
        ["sh", "-c", command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, env=environment, cwd=cwd, start_new_session=True)
    tail: list[str] = []
    killed = {"value": False}

    def kill():
        killed["value"] = True
        try:
            os.killpg(process.pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
    timer = threading.Timer(timeout, kill)
    timer.start()
    try:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            tail.append(line)
            del tail[:-80]
            if progress is not None:
                progress(line)
        returncode = process.wait()
    finally:
        timer.cancel()
    if killed["value"]:
        return 124, "\n".join(tail[-40:]) + f"\ncommand timed out after {timeout} seconds"
    return returncode, "\n".join(tail[-40:])


def _git(root: Path, *arguments: str, timeout: int = 120) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True,
        check=False, timeout=timeout,
        env=local_environment({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}))
    if result.returncode != 0:
        raise LocalImageError("local_images_source_unavailable",
                              f"git {arguments[0]} failed: {(result.stderr or '').strip()[:300]}")
    return (result.stdout or "").strip()


def resolve_commit(source_root: Path, revision: str | None) -> tuple[str, Path, str]:
    """Return (commit, checkout_root, source prefix) for one exact revision."""
    checkout = Path(_git(source_root, "rev-parse", "--show-toplevel")).resolve()
    try:
        prefix = Path(source_root).resolve().relative_to(checkout).as_posix()
    except ValueError:
        raise LocalImageError("local_images_source_unavailable",
                              "source_root is outside its Git checkout") from None
    wanted = revision or "HEAD"
    if wanted.startswith("-"):
        raise LocalImageError("local_images_source_unavailable", "revision is invalid")
    commit = _git(checkout, "rev-parse", "--verify", "--end-of-options", wanted + "^{commit}").lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise LocalImageError("local_images_source_unavailable",
                              "revision did not resolve to one commit")
    return commit, checkout, prefix


def materialize_checkout(checkout: Path, commit: str, destination: Path) -> None:
    """Create a clean detached checkout of one commit without touching the source repo."""
    result = subprocess.run(
        ["git", "clone", "--quiet", "--shared", "--no-checkout", str(checkout), str(destination)],
        capture_output=True, text=True, check=False, timeout=300,
        env=local_environment({"GIT_TERMINAL_PROMPT": "0"}))
    if result.returncode != 0:
        raise LocalImageError("local_images_source_unavailable",
                              f"could not clone the source checkout: {(result.stderr or '').strip()[:300]}")
    _git(destination, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", commit,
         timeout=600)


def _inspect_local(ref: str, environment: dict) -> dict | None:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .}}", ref],
        capture_output=True, text=True, check=False, timeout=60, env=environment)
    if result.returncode != 0:
        return None
    try:
        document = json.loads(result.stdout)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def build_local_images(*, validated: dict, commit: str, source_revision: str,
                       config_command: str, build_command, compose_project: str,
                       source_dir: str, runtime_dir: str, output_dir: Path,
                       platform: str, environment: dict, timeout: int,
                       source_root_relative: str = ".", progress=None,
                       symbolic_secrets=()) -> dict:
    """Observe, build/pull, save, and seal one receipt. Returns the receipt."""
    services = declared_services(validated)
    observation = parse_probe_output(_probe_local(
        probe_argv(config_command=config_command, services=list(services),
                   roots={"SOURCE": source_dir, "RUNTIME": runtime_dir}, engine=True,
                   symbolic_secrets=symbolic_secrets),
        environment))
    if observation.get("config_error"):
        raise LocalImageError("local_images_compose_config_unavailable",
                              f"local Compose config is {observation['config_error']}")
    observed = observation["services"]
    for name in services:
        item = observed.get(name) or {}
        if item.get("present") is not True or not item.get("image"):
            raise LocalImageError(
                "local_images_image_unresolved",
                f"service {name} must resolve to an explicit Compose image")
        if item.get("pull_policy") == "build":
            raise LocalImageError("local_images_image_unresolved",
                                  f"service {name} uses pull_policy: build")
    built = [name for name in services if observed[name]["build"]]
    built_refs = {observed[name]["image"] for name in built}
    definitions: dict[str, str] = {}
    for name in built:
        image = observed[name]["image"]
        previous = definitions.setdefault(image, observed[name]["build_digest"])
        if previous != observed[name]["build_digest"]:
            raise LocalImageError(
                "local_images_build_conflict",
                f"services sharing image {image} have different build definitions")
    if built:
        if progress is not None:
            progress(f"building {len(built)} service image(s) for {platform}: {', '.join(built)}")
        returncode, tail = _run_local(build_command(built), environment=environment,
                                      timeout=timeout, progress=progress)
        if returncode != 0:
            raise LocalImageError("local_images_build_failed",
                                  f"local Compose build exited {returncode}",
                                  detail=tail[-2000:])
    sources: dict[str, str] = {}
    for name in services:
        ref = observed[name]["image"]
        if name in built:
            sources[name] = "built"
        elif ref in built_refs:
            sources[name] = "shared"
        elif is_pinned(ref):
            sources[name] = "pinned"
        else:
            raise LocalImageError(
                "local_images_image_unpinned",
                f"non-built service {name} must use a digest-pinned image")
    # Group references by local image identity: one archive per image.
    groups: dict[str, dict] = {}
    for name in services:
        ref = observed[name]["image"]
        if sources[name] == "pinned":
            continue
        document = _inspect_local(ref, environment)
        if document is None:
            raise LocalImageError("local_images_image_unresolved",
                                  f"image {ref} for service {name} is not present after build")
        image_platform = f"{document.get('Os') or ''}/{document.get('Architecture') or ''}"
        if image_platform != platform:
            raise LocalImageError(
                "local_images_platform_mismatch",
                f"image {ref} is {image_platform}, not the target {platform}")
        image_id = document.get("Id")
        if not isinstance(image_id, str) or not _DIGEST_RE.fullmatch(image_id):
            raise LocalImageError("local_images_image_unresolved",
                                  f"image {ref} has no content-addressed ID")
        layers = (document.get("RootFS") or {}).get("Layers")
        group = groups.setdefault(image_id, {"refs": [], "layers": layers})
        if ref not in group["refs"]:
            group["refs"].append(ref)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for stale in output_dir.glob("image-*.tar"):
        stale.unlink()
    artifacts: dict[str, dict] = {}
    artifact_by_ref: dict[str, str] = {}
    for number, (image_id, group) in enumerate(sorted(groups.items()), start=1):
        key = f"image-{number}"
        archive = output_dir / f"{key}.tar"
        if progress is not None:
            progress(f"saving {', '.join(group['refs'])} to {archive.name}")
        returncode, tail = _run_local(
            shlex.join(["docker", "image", "save", "--platform", platform, "-o", str(archive),
                        *group["refs"]]),
            environment=environment, timeout=timeout)
        if returncode != 0:
            raise LocalImageError("local_images_save_failed",
                                  f"could not save {', '.join(group['refs'])}",
                                  detail=tail[-2000:])
        os.chmod(archive, 0o600)
        identity = archive_identity(archive, group["refs"])
        archive_sha256, archive_bytes = file_sha256(archive)
        artifacts[key] = {
            "refs": list(group["refs"]),
            "image_id": identity["config_digest"],
            "builder_image_id": image_id,
            "manifest_digest": identity["manifest_digest"],
            "config_digest": identity["config_digest"],
            "layers_digest": canonical_digest(group["layers"] if isinstance(group["layers"], list) else None),
            "platform": platform,
            "archive": archive.name,
            "archive_sha256": archive_sha256,
            "archive_bytes": archive_bytes,
        }
        for ref in group["refs"]:
            artifact_by_ref[ref] = key
    receipt_services = {
        name: {"role": role, "image": observed[name]["image"],
               "build_digest": observed[name]["build_digest"],
               "source": sources[name],
               "artifact": artifact_by_ref.get(observed[name]["image"])}
        for name, role in services.items()
    }
    builder = _builder_identity(environment, observation.get("engine"))
    receipt = seal_receipt({
        "schema": RECEIPT_SCHEMA,
        "project": validated["project"],
        "environment": validated["environment"],
        "compose_project": compose_project,
        "commit": commit,
        "source_revision": source_revision,
        "source_root_relative": source_root_relative,
        "platform": platform,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "builder": builder,
        "build_config_digest": canonical_digest(
            {name: service["build_digest"] for name, service in receipt_services.items()}),
        "services": receipt_services,
        "artifacts": artifacts,
    })
    validate_receipt(receipt)
    receipt_path = output_dir / RECEIPT_FILE_NAME
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=output_dir)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, receipt_path)
    return receipt


def _probe_local(argv: list[str], environment: dict) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False,
                            timeout=PROBE_DEADLINE_SECONDS + 30, env=environment)
    if result.returncode != 0:
        raise LocalImageError("local_images_observation_unavailable",
                              "local image observation failed")
    return result.stdout


def _builder_identity(environment: dict, engine: str | None) -> dict:
    def read(argv):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=False,
                                    timeout=30, env=environment)
        except (OSError, subprocess.SubprocessError):
            return None
        value = (result.stdout or "").strip()
        return value[:120] if result.returncode == 0 and value else None
    return {
        "engine_platform": engine,
        "engine_version": read(["docker", "version", "--format", "{{.Server.Version}}"]),
        "compose_version": read(["docker", "compose", "version", "--short"]),
    }


# ------------------------------------------------------------------
# Transfer
# ------------------------------------------------------------------

def stream_archive(ssh_argv: list[str], archive: Path, *, timeout: int, progress=None,
                   label: str = "", cancelled=None) -> dict:
    """Stream one archive into a remote command's stdin with a hard deadline.

    The archive is hashed while it is sent; the caller compares the digest with
    the receipt. A timeout kills the local SSH client; the remote load outcome
    is then unknown and is decided only by the following observation.
    """
    total = archive.stat().st_size
    digest = hashlib.sha256()
    sent = 0
    started = time.monotonic()
    timed_out = {"value": False}
    cancelled_state = {"value": False}
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(ssh_argv, stdin=subprocess.PIPE, stdout=output,
                                   stderr=subprocess.STDOUT)

        def kill():
            timed_out["value"] = True
            process.kill()
        timer = threading.Timer(timeout, kill)
        timer.start()
        broken = False
        try:
            assert process.stdin is not None
            next_report = 0
            with open(archive, "rb") as handle:
                while True:
                    if cancelled is not None and cancelled():
                        cancelled_state["value"] = True
                        process.kill()
                        break
                    chunk = handle.read(1 << 20)
                    if not chunk:
                        break
                    digest.update(chunk)
                    try:
                        process.stdin.write(chunk)
                    except (BrokenPipeError, OSError):
                        broken = True
                        break
                    sent += len(chunk)
                    if progress is not None and (sent >= next_report or sent == total):
                        elapsed = max(time.monotonic() - started, 0.001)
                        progress(f"{label}{sent // 1048576}/{total // 1048576} MiB "
                                 f"({sent * 100 // max(total, 1)}%, "
                                 f"{sent / 1048576 / elapsed:.1f} MiB/s)")
                        next_report = sent + max(total // 20, 64 << 20)
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                broken = True
            returncode = process.wait()
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
        output.seek(0)
        tail = output.read()[-4000:].decode("utf-8", errors="replace")
    return {"returncode": returncode, "timed_out": timed_out["value"],
            "cancelled": cancelled_state["value"], "broken_pipe": broken,
            "sent_bytes": sent, "total_bytes": total,
            "sha256": "sha256:" + digest.hexdigest() if sent == total else None,
            "seconds": round(time.monotonic() - started, 1), "output_tail": tail}
