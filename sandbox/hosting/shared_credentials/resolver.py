"""Read-only resolution of installation-owned credential execution records.

Records are obtained through the project-registry owner, never request paths or
direct registry JSON. The supervisor/lifecycle must register measured targets,
source artifacts and ALL native repository/key aliases before capable dispatch.
Missing registration is an explicit dependency, never a fabricated owner record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile

from sandbox.config.facade import project_identity
from sandbox.project_registry.json import JsonRegistryRepository
from sandbox.services.environment import ExplicitEnvironment
from sandbox.services.process import BoundedProcessRunner
from .consumers import CloudflareRecordAction, CloudflareRecordConsumer
from .models import CredentialIntent, InstallationBinding, TargetReference
from sandbox.hosting.recovery.repository import RecoveryRepository


REGISTRATION_FIELD = "hosting_credentials"
REGISTRATION_SCHEMA = 1
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ENTRIES = 10000


class ResolutionError(ValueError):
    def __init__(self, code="credential_registration_invalid"):
        self.code = code
        super().__init__(code)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _relative(raw):
    if (not isinstance(raw, str) or not raw or len(raw) > 1024
            or "\\" in raw or any(ord(c) < 32 for c in raw)):
        raise ResolutionError()
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("", ".", "..") for part in raw.split("/")):
        raise ResolutionError()
    examples = {".env.example", ".env.sample", ".env.template"}
    if any(part == ".git" or (part.startswith(".env") and part not in examples) for part in path.parts):
        raise ResolutionError("credential_artifact_secret_path_denied")
    return path


def _file(root, relative, *, limit):
    relative = _relative(relative)
    path = root
    for part in relative.parts:
        path = path / part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ResolutionError("credential_artifact_path_unsafe")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(descriptor, "rb")
    info = os.fstat(stream.fileno())
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_size > limit):
        stream.close()
        raise ResolutionError("credential_artifact_path_unsafe")
    return stream, info


def _observation(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True, slots=True)
class NativeMutationBinding:
    repository: str
    state_key: str

    @classmethod
    def parse(cls, raw, *, project, environment):
        if (type(raw) is not dict or set(raw) != {"repository", "state_key"}
                or raw["repository"] not in ("legacy", "owner")
                or not isinstance(raw["state_key"], str)):
            raise ResolutionError("credential_target_lock_registration_invalid")
        parts = raw["state_key"].split("/")
        if (len(parts) != 3 or parts[1:] != [project, environment]
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", parts[0]) is None
                or (raw["repository"] == "owner" and parts[0] != "owner")):
            raise ResolutionError("credential_target_lock_registration_invalid")
        return cls(raw["repository"], raw["state_key"])


@dataclass(frozen=True, slots=True)
class _RecipeNativeMetadata:
    project: str
    environment: str
    owner_native_mutation: tuple = field(repr=False)


@dataclass(frozen=True, slots=True)
class ResolvedCredentialOperation:
    project_identity: str
    canonical_target_key: str
    project: str
    environment: str
    target: TargetReference
    native_mutations: tuple[NativeMutationBinding, ...]
    registration_digest: str
    source_root: Path = field(repr=False)
    manifest: dict = field(repr=False)
    consumers: tuple = field(repr=False)
    native_ports: tuple = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class RecipeSelection:
    """Read-only selection resolved before actor/target preparation exists."""
    project_ref: str
    profile_ref: str
    environment: str
    source_commit: str
    name: str
    artifact_digest: str
    manifest_digest: str
    expected_installation: InstallationBinding


@dataclass(frozen=True, slots=True)
class RegisteredCredentialRecipe:
    """Measured server source metadata, never a lifetime claim or writer grant."""
    owner: object = field(repr=False)
    canonical: object = field(repr=False)
    name: str
    project_identity: str
    project: str
    environment: str
    source_commit: str
    source_root: Path = field(repr=False)
    checkout_root: Path = field(repr=False)
    manifest_path: Path = field(repr=False)
    manifest_digest: str
    source_tree: str
    artifact_digest: str
    artifact_path: Path = field(repr=False)
    registration_digest: str
    native_mutations: tuple[NativeMutationBinding, ...]
    native_ports: tuple = field(repr=False)
    owner_native_mutation: tuple = field(repr=False)
    manifest: dict = field(repr=False)
    consumers: tuple = field(repr=False)
    manifest_bytes: bytes = field(default=b"", repr=False)
    template_path: Path | None = field(default=None, repr=False)
    template_digest: str | None = None
    rendered_ports: tuple[tuple[str, int], ...] = ()


class RegisteredCredentialResolver:
    """Concrete server adapters; construction creates no files or authority."""

    def __init__(self, *, home: str | Path, ledger=None):
        self.home = Path(home).expanduser().resolve()
        self.registry = JsonRegistryRepository(self.home / "runtime" / "registry.json")
        if ledger is not None:
            from sandbox.jobs.credential_operations import CredentialOperationRepository
            if type(ledger) is not CredentialOperationRepository:
                raise ResolutionError("credential_ledger_invalid")
        self._ledger = ledger

    def _installation(self, intent):
        failed = None
        binding = None
        try:
            from sandbox.installation.binding import read_installation_binding, validate_current
            raw = read_installation_binding(home=self.home)
            binding = InstallationBinding.parse(raw)
            if binding != intent.expected_installation:
                raise ResolutionError("credential_installation_changed")
            validate_current(intent.expected_installation.as_dict(), home=self.home)
            if dict(binding.execution_capabilities).get("hosting.credentials") != 1:
                raise ResolutionError("credential_capability_unavailable")
        except ImportError:
            failed = "credential_installation_adapter_unavailable"
        except Exception as exc:
            code = getattr(exc, "code", "credential_installation_changed")
            failed = code if code in {"installation_binding_uninitialized", "credential_installation_changed",
                                      "credential_capability_unavailable"} else "credential_installation_changed"
        if failed:
            raise ResolutionError(failed) from None
        return binding

    def _project(self, reference):
        found = []
        info = self.registry.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o022):
            raise ResolutionError("credential_project_registry_unsafe")
        # No transaction/migration, fallback root or client path is used here.
        for record in self.registry.read_only_all().values():
            identity = project_identity(record, label=record["label"])
            if identity["identity"] == reference:
                found.append((record, identity))
        if len(found) != 1:
            raise ResolutionError("credential_project_registration_unavailable")
        record, identity = found[0]
        root = Path(identity["canonical_root"])
        info = root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.geteuid() or info.st_mode & 0o022):
            raise ResolutionError("credential_project_registration_unsafe")
        return record, identity, root

    def _git(self, root, *arguments, limit=1024 * 1024):
        # Pure object reads only. No credentials, hooks, index refresh, remote,
        # working-tree filters or inherited owner environment can be invoked.
        env = ExplicitEnvironment({"PATH": os.defpath, "LANG": "C", "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"})
        result = BoundedProcessRunner(max_output=limit).run(
            ["git", "--no-optional-locks", "-c", "core.hooksPath=" + os.devnull,
             "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
            env=env, timeout=15)
        if (result.returncode or result.stdout_truncated or result.stderr_truncated
                or result.termination_reason is not None):
            raise ResolutionError("credential_source_object_unavailable")
        return result.stdout

    def _artifact(self, root, registration, intent, *, checkout_root, source_tree):
        commit = self._git(checkout_root, "rev-parse", "--verify", intent.source_commit + "^{commit}", limit=4096).strip()
        if commit != intent.source_commit:
            raise ResolutionError("credential_source_changed")
        relative_source = root.relative_to(checkout_root).as_posix()
        selected = intent.source_commit + ("^{tree}" if relative_source == "." else ":" + relative_source)
        if self._git(checkout_root, "rev-parse", "--verify", selected, limit=4096).strip() != source_tree:
            raise ResolutionError("credential_source_tree_changed")
        listing = self._git(checkout_root, "ls-tree", "-r", "-z", source_tree)
        blobs = {}
        for entry in listing.split("\0"):
            if not entry:
                continue
            metadata, name = entry.split("\t", 1)
            mode, kind, object_id = metadata.split(" ")
            name = str(_relative(name))
            if kind != "blob" or mode not in ("100644", "100755"):
                raise ResolutionError("credential_source_entry_unsupported")
            blobs[name] = object_id
        stream, before = _file(root, registration["artifact_relative"], limit=MAX_ARTIFACT_BYTES)
        seen = set()
        digest = hashlib.sha256()
        with stream:
            count = 0
            for chunk in iter(lambda: stream.read(128 * 1024), b""):
                count += len(chunk)
                if count > MAX_ARTIFACT_BYTES:
                    raise ResolutionError("credential_artifact_limit")
                digest.update(chunk)
            if digest.hexdigest() != intent.artifact_digest:
                raise ResolutionError("credential_artifact_changed")
            stream.seek(0)
            total = 0
            with tarfile.open(fileobj=stream, mode="r:") as archive:
                for number, item in enumerate(archive):
                    if number >= MAX_ENTRIES:
                        raise ResolutionError("credential_artifact_limit")
                    if item.isdir():
                        _relative(item.name.rstrip("/"))
                        continue
                    name = str(_relative(item.name))
                    if (not item.isfile() or name in seen or name not in blobs
                            or item.size > MAX_ARTIFACT_BYTES or item.size < 0):
                        raise ResolutionError("credential_artifact_invalid")
                    seen.add(name)
                    total += item.size
                    if total > MAX_ARTIFACT_BYTES:
                        raise ResolutionError("credential_artifact_limit")
                    blob = hashlib.new("sha256" if len(blobs[name]) == 64 else "sha1")
                    blob.update(b"blob " + str(item.size).encode("ascii") + b"\0")
                    member = archive.extractfile(item)
                    with member:
                        for chunk in iter(lambda: member.read(128 * 1024), b""):
                            blob.update(chunk)
                    if blob.hexdigest() != blobs[name]:
                        raise ResolutionError("credential_artifact_source_mismatch")
            if _observation(before) != _observation(os.fstat(stream.fileno())):
                raise ResolutionError("credential_artifact_changed")
        if not seen or seen != set(blobs):
            raise ResolutionError("credential_artifact_invalid")

    @staticmethod
    def _manifest(content):
        import yaml
        from sandbox.hosting.credential_selection import parse_declarations
        for index, token in enumerate(yaml.scan(content)):
            if index >= 16384 or isinstance(token, yaml.tokens.AliasToken):
                raise ResolutionError("credential_manifest_invalid")
        class Loader(yaml.SafeLoader):
            def construct_mapping(self, node, deep=False):
                result = {}
                for key_node, value_node in node.value:
                    key = self.construct_object(key_node, deep=deep)
                    if not isinstance(key, str) or key in result:
                        raise ResolutionError("credential_manifest_invalid")
                    result[key] = self.construct_object(value_node, deep=deep)
                return result
        manifest = yaml.load(content, Loader=Loader)
        if not isinstance(manifest, dict):
            raise ResolutionError("credential_manifest_invalid")
        # Parse every environment to reject inline secret values before any
        # surrounding validator could echo their content. No values are read.
        for name in manifest.get("environments", {}):
            parse_declarations(manifest, name)
        return manifest

    def resolve(self, intent: CredentialIntent) -> ResolvedCredentialOperation:
        failed = None
        result = None
        try:
            result = self._resolve(intent)
        except ResolutionError as exc:
            failed = exc.code
        except Exception:
            failed = "credential_registration_unavailable"
        if failed:
            raise ResolutionError(failed) from None
        return result

    def _resolve(self, intent):
        if intent.registration_revision is None:
            raise ResolutionError("credential_registration_revision_required")
        if intent.action == "stack_up":
            recipe = self.read_recipe(intent)
            if recipe.registration_digest != intent.registration_revision:
                raise ResolutionError("credential_registration_changed")
            if self._ledger is None:
                raise ResolutionError("credential_target_preparation_unavailable")
            preparation = self._ledger.target_preparation(intent.request_scope())
            if (preparation is None or preparation.canonical_intent != intent.canonical_json()
                    or preparation.project_identity != recipe.project_identity
                    or preparation.project != recipe.project):
                raise ResolutionError("credential_target_preparation_changed")
            return ResolvedCredentialOperation(recipe.project_identity, recipe.canonical.key,
                recipe.project, recipe.environment, intent.target_ref, recipe.native_mutations,
                recipe.registration_digest, recipe.source_root, recipe.manifest,
                recipe.consumers, recipe.native_ports)
        self._installation(intent)
        record, identity, root = self._project(intent.project_ref)
        from sandbox.application.context import load_project_descriptor
        from .profiles import registered_profiles
        config = load_project_descriptor(root)
        profiles = registered_profiles((config.get("secrets") or {}).get("hostingProfiles", {}))
        profile = profiles.get(intent.profile_ref)
        if profile is None or not profile.matches(intent):
            raise ResolutionError("credential_profile_changed")
        registrations = record.get(REGISTRATION_FIELD)
        if (type(registrations) is not dict or set(registrations) - {"schema_version", "targets", "recipes", "recipe_instances"}
                or type(registrations["schema_version"]) is not int
                or registrations["schema_version"] != REGISTRATION_SCHEMA
                or type(registrations["targets"]) is not dict):
            raise ResolutionError("credential_target_registration_unavailable")
        registration = registrations["targets"].get(intent.target_ref.target_id)
        fields = {"project", "environment", "target_ref", "canonical_target_key", "source_commit",
                  "artifact_relative", "artifact_digest", "manifest_digest",
                  "native_mutations", "consumer_actions"}
        if type(registration) is not dict or set(registration) != fields:
            raise ResolutionError("credential_target_registration_unavailable")
        target = TargetReference.parse(registration["target_ref"])
        if (target != intent.target_ref or registration["environment"] != intent.environment
                or registration["source_commit"] != intent.source_commit
                or registration["artifact_digest"] != intent.artifact_digest
                or registration["manifest_digest"] != intent.manifest_digest
                or not _digest(registration["canonical_target_key"])):
            raise ResolutionError("credential_target_registration_changed")
        from sandbox.hosting.owner_targets import read_registered_owner_target, require_current
        hosted = read_registered_owner_target(name=target.target_id)
        owner, canonical = hosted.owner, hosted.canonical
        if (owner.machine_identity != intent.expected_installation.machine_identity
                or owner.home != self.home or owner.revision != intent.expected_installation.runtime_revision):
            raise ResolutionError("credential_installation_changed")
        if canonical.key != registration["canonical_target_key"]:
            raise ResolutionError("credential_target_registration_changed")
        if ((hosted.name, hosted.copy_id, hosted.incarnation, hosted.project, hosted.environment,
             hosted.source_commit, hosted.manifest_digest) !=
                (target.target_id, target.copy_id, target.incarnation, registration["project"],
                 intent.environment, intent.source_commit, intent.manifest_digest)
                or root != hosted.source_root):
            raise ResolutionError("credential_host_target_changed")
        if require_current(hosted) != hosted:
            raise ResolutionError("credential_host_target_changed")
        native = registration["native_mutations"]
        if type(native) is not list or not 1 <= len(native) <= 64:
            raise ResolutionError("credential_target_lock_registration_unavailable")
        mutations = tuple(NativeMutationBinding.parse(item, project=registration["project"],
            environment=intent.environment) for item in native)
        if len(set(mutations)) != len(mutations) or not any(item.repository == "owner" for item in mutations):
            raise ResolutionError("credential_target_lock_registration_invalid")
        ports, observed = self._native_ports(owner, hosted, intent.expected_installation)
        if set(mutations) != set(observed):
            raise ResolutionError("credential_target_lock_registration_changed")
        self._artifact(root, registration, intent,
                       checkout_root=hosted.checkout_root, source_tree=hosted.source_tree)
        manifest_relative = hosted.manifest_path.relative_to(owner.home).as_posix()
        stream, before = _file(owner.home, manifest_relative, limit=MAX_MANIFEST_BYTES)
        with stream:
            manifest_bytes = stream.read(MAX_MANIFEST_BYTES + 1)
            if (len(manifest_bytes) > MAX_MANIFEST_BYTES
                    or _observation(before) != _observation(os.fstat(stream.fileno()))
                    or hashlib.sha256(manifest_bytes).hexdigest() != intent.manifest_digest):
                raise ResolutionError("credential_manifest_changed")
        manifest = self._manifest(manifest_bytes)
        if manifest.get("project") != registration["project"]:
            raise ResolutionError("credential_manifest_changed")
        environment = manifest.get("environments", {}).get(intent.environment)
        if type(environment) is not dict or type(environment.get("host")) is not dict:
            raise ResolutionError("credential_manifest_invalid")
        hosts = environment["host"]
        hostnames = [hosts.get("primary")]
        aliases = hosts.get("aliases", [])
        if type(aliases) is not list or len(aliases) > 128:
            raise ResolutionError("credential_manifest_invalid")
        for alias in aliases:
            if type(alias) is not dict:
                raise ResolutionError("credential_manifest_invalid")
            hostnames.append(alias.get("hostname"))
        consumers = self._consumers(intent, registration["consumer_actions"], hostnames)
        registration_digest = hashlib.sha256(json.dumps(registration, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if registration_digest != intent.registration_revision:
            raise ResolutionError("credential_registration_changed")
        self._installation(intent)
        return ResolvedCredentialOperation(identity["identity"], canonical.key,
            registration["project"], intent.environment, target, mutations,
            registration_digest, root, manifest, consumers, ports)

    def read_recipe(self, intent: CredentialIntent) -> RegisteredCredentialRecipe:
        """Resolve a fresh target's source BEFORE any host/lifetime claim.

        Project root, checkout, manifest, artifact and native aliases come from
        current server-owned registrations and measured source objects. No
        existing-target fallback or synthesized registered target is used.
        The returned data confers no actor, preparation or writer authority.
        """
        failed, result = None, None
        try:
            result = self._read_recipe(intent)
        except ResolutionError as exc:
            failed = exc.code
        except Exception:
            failed = "credential_recipe_registration_unavailable"
        if failed:
            raise ResolutionError(failed) from None
        return result

    def read_invocation_recipe(self, *, project_ref: str, profile_ref: str,
                               environment: str, source_commit: str,
                               name: str) -> RegisteredCredentialRecipe:
        """Metadata before actor admission; creates no intent or target identity.

        Exact source/artifact selectors are obtained from the registered profile,
        not a fabricated CredentialIntent, actor, copy/incarnation or context.
        """
        failed, result = None, None
        try:
            from sandbox.application.context import load_project_descriptor
            from sandbox.installation.binding import read_installation_binding
            from .profiles import registered_profiles
            if (not isinstance(name, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name) is None
                    or not isinstance(profile_ref, str) or not isinstance(project_ref, str)):
                raise ResolutionError("credential_recipe_selection_invalid")
            _record, _identity, root = self._project(project_ref)
            profiles = registered_profiles((load_project_descriptor(root).get("secrets") or {})
                                           .get("hostingProfiles", {}))
            profile = profiles.get(profile_ref)
            if profile is None or profile.environment != environment or profile.source_commit != source_commit:
                raise ResolutionError("credential_profile_changed")
            binding = InstallationBinding.parse(read_installation_binding(home=self.home))
            selection = RecipeSelection(project_ref, profile_ref, environment, source_commit, name,
                profile.artifact_digest, profile.manifest_digest, binding)
            result = self._read_recipe(selection)
        except ResolutionError as exc:
            failed = exc.code
        except Exception:
            failed = "credential_recipe_registration_unavailable"
        if failed:
            raise ResolutionError(failed) from None
        return result

    def _read_recipe(self, intent, *, candidate_registration=None):
        from sandbox.application.context import load_project_descriptor
        from sandbox.hosting.endpoints.local import owner_local, require_current
        from sandbox.hosting.endpoints.authority import CanonicalTarget
        from sandbox.hosting.endpoints.repository import owner_repository, owner_state_key
        from .profiles import registered_profiles
        binding = self._installation(intent)
        record, identity, root = self._project(intent.project_ref)
        profile = registered_profiles((load_project_descriptor(root).get("secrets") or {})
                                      .get("hostingProfiles", {})).get(intent.profile_ref)
        if (profile is None or (type(intent) is CredentialIntent and not profile.matches(intent))
                or profile.environment != intent.environment or profile.source_commit != intent.source_commit
                or profile.artifact_digest != intent.artifact_digest
                or (profile.template_relative is None and profile.manifest_digest != intent.manifest_digest)):
            raise ResolutionError("credential_profile_changed")
        catalog = record.get(REGISTRATION_FIELD)
        if (candidate_registration is None and (type(catalog) is not dict
                or catalog.get("schema_version") != REGISTRATION_SCHEMA
                or type(catalog.get("recipes")) is not dict)):
            raise ResolutionError("credential_recipe_registration_unavailable")
        name = intent.target_ref.target_id if type(intent) is CredentialIntent else intent.name
        if candidate_registration is not None:
            registration = candidate_registration
        elif profile.template_relative is not None:
            variants = catalog.get("recipe_instances")
            if type(variants) is not dict:
                raise ResolutionError("credential_named_recipe_unavailable")
            registration = variants.get(intent.profile_ref + "/" + name)
        else:
            registration = catalog["recipes"].get(intent.profile_ref)
        fields = {"name", "project", "environment", "source_commit", "artifact_relative",
                  "artifact_digest", "manifest_relative", "manifest_digest",
                  "native_mutations", "consumer_actions"}
        if profile.template_relative is not None:
            fields.add("render")
        if type(registration) is not dict or set(registration) != fields:
            raise ResolutionError("credential_recipe_registration_unavailable")
        if (registration["name"] != name or registration["environment"] != intent.environment
                or registration["source_commit"] != intent.source_commit
                or registration["artifact_digest"] != intent.artifact_digest
                or (type(intent) is CredentialIntent and registration["manifest_digest"] != intent.manifest_digest)):
            raise ResolutionError("credential_recipe_registration_changed")
        owner = owner_local()
        if (owner.home != self.home or owner.machine_identity != binding.machine_identity
                or owner.revision != binding.runtime_revision):
            raise ResolutionError("credential_installation_changed")
        canonical = CanonicalTarget.for_owner(owner, registration["project"], intent.environment)
        checkout = Path(self._git(root, "rev-parse", "--show-toplevel", limit=4096).strip())
        if (not checkout.is_absolute() or checkout != checkout.resolve(strict=True)
                or checkout == Path("/")):
            raise ResolutionError("credential_source_changed")
        relative = root.relative_to(checkout).as_posix()
        selector = intent.source_commit + ("^{tree}" if relative == "." else ":" + relative)
        tree = self._git(checkout, "rev-parse", "--verify", selector, limit=4096).strip()
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", tree) is None:
            raise ResolutionError("credential_source_changed")
        self._artifact(root, registration, intent, checkout_root=checkout, source_tree=tree)
        manifest_path = root / _relative(registration["manifest_relative"])
        stream, before = _file(root, registration["manifest_relative"], limit=MAX_MANIFEST_BYTES)
        with stream:
            content = stream.read(MAX_MANIFEST_BYTES + 1)
            if (len(content) > MAX_MANIFEST_BYTES
                    or _observation(before) != _observation(os.fstat(stream.fileno()))
                    or hashlib.sha256(content).hexdigest() != (
                        profile.manifest_digest if profile.template_relative is not None else registration["manifest_digest"])):
                raise ResolutionError("credential_manifest_changed")
        # The recipe manifest is part of the measured source tree. A dirty
        # working file cannot change mappings while the artifact stays pinned.
        manifest_blob = self._git(checkout, "rev-parse", "--verify",
            tree + ":" + str(_relative(registration["manifest_relative"])), limit=4096).strip()
        algorithm = "sha256" if len(manifest_blob) == 64 else "sha1"
        measured_blob = hashlib.new(algorithm, b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        if measured_blob != manifest_blob:
            raise ResolutionError("credential_manifest_source_mismatch")
        template_path, template_digest, rendered_ports = None, None, ()
        if profile.template_relative is not None:
            from .recipe_registration import render_named_recipe
            if registration["manifest_relative"] != profile.template_relative:
                raise ResolutionError("credential_recipe_template_changed")
            manifest, content = render_named_recipe(root=root, checkout=checkout, name=name,
                profile=profile, template_content=content, render=registration["render"])
            if hashlib.sha256(content).hexdigest() != registration["manifest_digest"]:
                raise ResolutionError("credential_manifest_changed")
            template_path = manifest_path
            template_digest = profile.manifest_digest
            rendered_ports = tuple(sorted(registration["render"]["ports"].items()))
            manifest_path = owner.home / "runtime" / "stacks" / name / "sandbox.hosting.yml"
        else:
            manifest = self._manifest(content)
        if manifest.get("project") != registration["project"]:
            raise ResolutionError("credential_manifest_changed")
        environment = manifest.get("environments", {}).get(intent.environment)
        if type(environment) is not dict or type(environment.get("host")) is not dict:
            raise ResolutionError("credential_manifest_invalid")
        hosts = environment["host"]
        aliases = hosts.get("aliases", [])
        if type(aliases) is not list or any(type(item) is not dict for item in aliases):
            raise ResolutionError("credential_manifest_invalid")
        hostnames = [hosts.get("primary"), *(item.get("hostname") for item in aliases)]
        if type(intent) is CredentialIntent:
            consumers = self._consumers(intent, registration["consumer_actions"], hostnames)
        else:
            raw_actions = registration["consumer_actions"]
            if type(raw_actions) is not dict or len(raw_actions) > 128:
                raise ResolutionError("credential_consumer_registration_invalid")
            if any(key != "cloudflare-dns-address" or type(raw) is not dict
                   for key, raw in raw_actions.items()):
                raise ResolutionError("credential_consumer_registration_invalid")
            consumers = tuple(CloudflareRecordAction(**raw) for raw in raw_actions.values())
            if any(action.hostname not in hostnames for action in consumers):
                raise ResolutionError("credential_consumer_target_changed")
        mutations = registration["native_mutations"]
        if type(mutations) is not list or not 1 <= len(mutations) <= 64:
            raise ResolutionError("credential_target_lock_registration_unavailable")
        mutations = tuple(NativeMutationBinding.parse(item, project=registration["project"],
            environment=intent.environment) for item in mutations)
        owner_port = (owner_repository(owner), owner_state_key(registration["project"], intent.environment))
        # _native_ports needs only observed server identity/project/native owner
        # binding, not a claimed target. This is typed metadata, never a grant.
        metadata = _RecipeNativeMetadata(registration["project"], intent.environment, owner_port)
        ports, observed = self._native_ports(owner, metadata, binding)
        if len(set(mutations)) != len(mutations) or set(mutations) != set(observed):
            raise ResolutionError("credential_target_lock_registration_changed")
        digest = hashlib.sha256(json.dumps(registration, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        require_current(owner)
        self._installation(intent)
        return RegisteredCredentialRecipe(owner, canonical, name, identity["identity"], registration["project"],
            intent.environment, intent.source_commit, root, checkout, manifest_path,
            registration["manifest_digest"], tree, intent.artifact_digest,
            root / _relative(registration["artifact_relative"]), digest, mutations,
            ports, owner_port, manifest, consumers, content, template_path, template_digest, rendered_ports)

    def _native_ports(self, owner, hosted, binding):
        from sandbox.core._remote import list_remotes
        from sandbox.hosting.endpoints.repository import owner_repository, owner_state_key
        owner_repository_value = owner_repository(owner)
        owner_key = owner_state_key(hosted.project, hosted.environment)
        # The owner reader's concrete native binding must agree with the actual
        # repository adapter, including its lock DIRECTORY, not just key text.
        recorded_repository, recorded_key = hosted.owner_native_mutation
        if (recorded_key != owner_key
                or recorded_repository.state_path != owner_repository_value.state_path
                or recorded_repository.lock_dir != owner_repository_value.lock_dir):
            raise ResolutionError("credential_target_lock_registration_changed")
        ports = [(owner_repository_value, owner_key)]
        references = [NativeMutationBinding("owner", owner_key)]
        legacy = RecoveryRepository(self.home / "runtime" / "hosts.json",
                                    self.home / "runtime" / "hosting" / "locks")
        state = legacy.load()
        remotes = list_remotes()
        suffix = "/" + hosted.project + "/" + hosted.environment
        for state_key in sorted(state.get("hosts", {})):
            if not isinstance(state_key, str) or not state_key.endswith(suffix):
                continue
            alias = state_key[:-len(suffix)]
            if not alias or "/" in alias:
                raise ResolutionError("credential_target_alias_identity_unavailable")
            entry = remotes.get(alias)
            if type(entry) is not dict:
                raise ResolutionError("credential_target_alias_identity_unavailable")
            identity, service = entry.get("control_identity"), entry.get("mcp_service")
            if (type(identity) is not dict or identity.get("schema_version") != 1
                    or type(service) is not dict
                    or not isinstance(identity.get("target_identity"), str)
                    or re.fullmatch(r"[0-9a-f]{24}", identity["target_identity"]) is None
                    or not isinstance(service.get("ownership_marker"), str)
                    or re.fullmatch(r"[0-9a-f]{24}", service["ownership_marker"]) is None):
                raise ResolutionError("credential_target_alias_identity_unavailable")
            # Existing authenticated pins distinguish another machine or another
            # installation on this machine. Never query SSH or invent alias trust.
            if (identity["target_identity"] != owner.machine_identity
                    or service["ownership_marker"] != binding.ownership_marker):
                continue
            ports.append((legacy, state_key))
            references.append(NativeMutationBinding("legacy", state_key))
        return tuple(ports), tuple(references)

    @staticmethod
    def _consumers(intent, actions, hostnames):
        if type(actions) is not dict:
            raise ResolutionError("credential_consumer_registration_invalid")
        consumers = []
        for consumer_id, raw in actions.items():
            if (consumer_id != "cloudflare-dns-address" or type(raw) is not dict
                    or set(raw) != {"operation", "zone_id", "hostname", "address", "proxied", "record_id"}):
                raise ResolutionError("credential_consumer_registration_invalid")
            action = CloudflareRecordAction(**raw)
            if action.hostname not in hostnames:
                raise ResolutionError("credential_consumer_target_changed")
            consumer = CloudflareRecordConsumer(intent.intent_digest(), action)
            if not consumer.matches(intent):
                raise ResolutionError("credential_consumer_registration_changed")
            consumers.append(consumer)
        return tuple(consumers)

    def validate_consumer(self, intent, consumer):
        current = self.resolve(intent)
        if type(consumer) is not CloudflareRecordConsumer or consumer not in current.consumers:
            raise ResolutionError("credential_consumer_registration_changed")


__all__ = ["RegisteredCredentialResolver", "ResolvedCredentialOperation",
           "RegisteredCredentialRecipe", "RecipeSelection", "NativeMutationBinding", "ResolutionError"]
