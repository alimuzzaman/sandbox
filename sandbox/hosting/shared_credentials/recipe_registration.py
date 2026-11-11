"""Authenticated, measured nonsecret recipe publication through registry CAS.

This is metadata planning only. It allocates no lifetime/capacity/host resource,
stages no source or manifest, and never reads provider credentials. A named plan
is frozen before the original execution invocation and credential admission.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re

from sandbox.application.context import load_project_descriptor
from sandbox.hosting import stack_registry, stack_template
from sandbox.project_registry.json import RegistryUpdateConflict
from .authentication import require_authenticated
from .profiles import registered_profiles


def render_named_recipe(*, root, checkout, name, profile, template_content, render):
    from .application import _yaml
    from .resolver import ResolutionError
    if (type(render) is not dict or set(render) != {"name", "ports", "ref_branch"}
            or render["name"] != name or type(render["ports"]) is not dict
            or any(type(value) is not int or not 1024 <= value <= 65535
                   for value in render["ports"].values())
            or not isinstance(render["ref_branch"], str)):
        raise ResolutionError("credential_recipe_render_invalid")
    raw = _yaml(template_content)
    if type(raw) is not dict or type(raw.get("environments")) is not dict:
        raise ResolutionError("credential_recipe_render_invalid")
    for environment in raw["environments"].values():
        if (type(environment) is not dict or type(environment.get("secrets", {})) is not dict
                or "values" in environment.get("secrets", {})):
            raise ResolutionError("credential_recipe_inline_values_denied")
    template = stack_template.parse_template(raw)
    if set(render["ports"]) != set(template.ports):
        raise ResolutionError("credential_recipe_render_invalid")
    template_relative_to_checkout = (Path(root) / profile.template_relative).relative_to(checkout).as_posix()
    declared_root = stack_template.template_source_root(template, template_relative_to_checkout)
    if (Path(checkout) / declared_root).resolve() != Path(root):
        raise ResolutionError("credential_recipe_source_changed")
    ref = stack_template.StackRef(profile.source_commit, profile.source_commit, render["ref_branch"])
    manifest = stack_template.render(template, slug=name, ref=ref, ports=render["ports"],
        source_root=root, template_path=Path(root) / profile.template_relative)
    from sandbox.hosting.credential_selection import parse_declarations
    parse_declarations(manifest, profile.environment)
    content = (json.dumps(manifest, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode()
    return manifest, content


def register_recipe(actor, *, resolver, project_ref, profile_ref, name,
                    artifact_relative, manifest_relative, consumer_actions, ref_branch=""):
    """Fixed metadata operation. The actual control transport supplies actor.

    Relative source references are checked inside the existing registered root;
    no caller root, executable, authority callback or credential is accepted.
    Existing same-name plans are immutable. Conflicts never select another name,
    delete a reservation, or adopt unknown historical lifetime evidence.
    """
    from .resolver import (
        RegisteredCredentialResolver, RecipeSelection, ResolutionError,
        REGISTRATION_FIELD, _RecipeNativeMetadata, _file, _relative,
        MAX_MANIFEST_BYTES,
    )
    from .models import InstallationBinding
    from sandbox.installation.binding import read_installation_binding, validate_current
    from sandbox.hosting.endpoints.local import owner_local, require_current
    from sandbox.hosting.endpoints.authority import CanonicalTarget
    from sandbox.hosting.endpoints.repository import owner_repository, owner_state_key
    require_authenticated(actor)
    if (type(resolver) is not RegisteredCredentialResolver or not isinstance(name, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name) is None
            or type(consumer_actions) is not dict):
        raise ResolutionError("credential_recipe_selection_invalid")
    record, identity, root = resolver._project(project_ref)
    profile = registered_profiles((load_project_descriptor(root).get("secrets") or {})
                                  .get("hostingProfiles", {})).get(profile_ref)
    if profile is None or profile.action != "stack_up":
        raise ResolutionError("credential_profile_changed")
    binding = InstallationBinding.parse(read_installation_binding(home=resolver.home))
    owner = owner_local()
    if (owner.home != resolver.home or owner.machine_identity != binding.machine_identity
            or owner.revision != binding.runtime_revision):
        raise ResolutionError("credential_installation_changed")
    _relative(artifact_relative)
    _relative(manifest_relative)
    if profile.template_relative is not None and manifest_relative != profile.template_relative:
        raise ResolutionError("credential_recipe_template_changed")
    stream, _before = _file(root, manifest_relative, limit=MAX_MANIFEST_BYTES)
    with stream:
        template_content = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(template_content) > MAX_MANIFEST_BYTES or hashlib.sha256(template_content).hexdigest() != profile.manifest_digest:
        raise ResolutionError("credential_manifest_changed")
    checkout = Path(resolver._git(root, "rev-parse", "--show-toplevel", limit=4096).strip())
    if not checkout.is_absolute() or checkout != checkout.resolve(strict=True):
        raise ResolutionError("credential_source_changed")
    if ref_branch:
        if (not isinstance(ref_branch, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", ref_branch) is None
                or resolver._git(checkout, "rev-parse", "--verify", "refs/heads/" + ref_branch + "^{commit}",
                                 limit=4096).strip() != profile.source_commit):
            raise ResolutionError("credential_recipe_branch_changed")
    catalog = copy.deepcopy(record.get(REGISTRATION_FIELD) or
        {"schema_version": 1, "targets": {}, "recipes": {}, "recipe_instances": {}})
    if (type(catalog) is not dict or catalog.get("schema_version") != 1
            or set(catalog) - {"schema_version", "targets", "recipes", "recipe_instances"}):
        raise ResolutionError("credential_recipe_registration_invalid")
    for key in ("targets", "recipes", "recipe_instances"):
        if type(catalog.setdefault(key, {})) is not dict:
            raise ResolutionError("credential_recipe_registration_invalid")
    key = profile_ref + "/" + name
    existing = catalog["recipe_instances"].get(key) if profile.template_relative else catalog["recipes"].get(profile_ref)
    if existing is not None:
        # Repeat only the original fully measured selection. Never repair a
        # frozen named plan in place, including after a no-effects refusal.
        if (existing.get("name") != name or existing.get("artifact_relative") != artifact_relative
                or existing.get("manifest_relative") != manifest_relative
                or existing.get("consumer_actions") != consumer_actions
                or existing.get("render", {}).get("ref_branch", "") != ref_branch):
            raise ResolutionError("credential_recipe_registration_conflict")
        return resolver.read_invocation_recipe(project_ref=project_ref, profile_ref=profile_ref,
            environment=profile.environment, source_commit=profile.source_commit, name=name)
    render = None
    if profile.template_relative is not None:
        from .application import _yaml
        template = stack_template.parse_template(_yaml(template_content))
        existing_stacks = stack_registry.read(resolver.home / "runtime/stacks/registry.json")
        reserved = {port for item in existing_stacks.get("stacks", {}).values() if type(item) is dict
                    for port in (item.get("ports") or {}).values() if type(port) is int}
        # This chooses nonbinding candidates only. The admitted host claim must
        # atomically recheck actual availability before creating resources.
        ports = stack_registry.allocate_ports(remote_name=binding.installation_id, slug=name,
            names=template.ports, existing={}, pins={}, other_stacks={}, reserved=reserved,
            bound={}, own_compose_project="planned-" + name)
        render = {"name": name, "ports": ports, "ref_branch": ref_branch}
        manifest, content = render_named_recipe(root=root, checkout=checkout, name=name,
            profile=profile, template_content=template_content, render=render)
    else:
        manifest, content = resolver._manifest(template_content), template_content
        if manifest.get("project") != name:
            raise ResolutionError("credential_recipe_name_requires_template")
    project = manifest.get("project")
    canonical = CanonicalTarget.for_owner(owner, project, profile.environment)
    owner_port = (owner_repository(owner), owner_state_key(project, profile.environment))
    _ports, native = resolver._native_ports(owner, _RecipeNativeMetadata(project, profile.environment, owner_port), binding)
    descriptor = {"name": name, "project": project, "environment": profile.environment,
        "source_commit": profile.source_commit, "artifact_relative": artifact_relative,
        "artifact_digest": profile.artifact_digest, "manifest_relative": manifest_relative,
        "manifest_digest": hashlib.sha256(content).hexdigest(),
        "native_mutations": [{"repository": item.repository, "state_key": item.state_key} for item in native],
        "consumer_actions": copy.deepcopy(consumer_actions)}
    if render is not None:
        descriptor["render"] = render
        catalog["recipe_instances"][key] = descriptor
    else:
        catalog["recipes"][profile_ref] = descriptor
    selection = RecipeSelection(project_ref, profile_ref, profile.environment, profile.source_commit,
        name, profile.artifact_digest, profile.manifest_digest, binding)
    # Full source/blob/action/native validation BEFORE short publication CAS.
    measured = resolver._read_recipe(selection, candidate_registration=descriptor)
    if measured.canonical != canonical:
        raise ResolutionError("credential_recipe_registration_changed")
    require_authenticated(actor)
    require_current(owner)
    validate_current(binding.as_dict(), home=resolver.home)
    try:
        resolver.registry.update_if_current(str(root), record["label"], expected=record,
                                             changes={REGISTRATION_FIELD: catalog})
    except RegistryUpdateConflict:
        raise ResolutionError("credential_recipe_registration_conflict") from None
    return resolver.read_invocation_recipe(project_ref=project_ref, profile_ref=profile_ref,
        environment=profile.environment, source_commit=profile.source_commit, name=name)


__all__ = ["register_recipe", "render_named_recipe"]
