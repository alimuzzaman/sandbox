"""One fail-closed, atomic multi-image activation transaction."""

from __future__ import annotations

import time
from contextlib import nullcontext

from .models import ActivationContractError, activation_digest
from .v2_models import (
    ActivationRequestV2, GenerationBoundEdgeReceiptV2,
    ReplacementIntentV2, RollbackCompatibilityGrantV2, VerifiedActivationGenerationV2,
    validate_activation_generation,
)
from .v2_runtime import RuntimeObserverV2, plan_set_bindings, validate_rendered_topology_v2


class ActivationServiceV2:
    """Coordinates custody, one Compose effect, observation, edge, and commit.

    The repository is the target-lock owner.  Its v2 methods must atomically pin
    the complete proof set before returning from ``accept_v2`` and must not
    advance the generation until ``commit_v2``.
    """

    def __init__(self, *, repository, runtime_adapter, edge_adapter,
                 rollback_grant_verifier, clock=None) -> None:
        self.repository = repository
        self.runtime_adapter = runtime_adapter
        self.runtime_observer = RuntimeObserverV2(runtime_adapter)
        self.edge_adapter = edge_adapter
        self.rollback_grant_verifier = rollback_grant_verifier
        self.clock = clock or time.time

    def execute(self, request: ActivationRequestV2, *, rollback_grant,
                compose_files: tuple[str, ...], compose_project: str,
                edge_route_digest: str, admission_deadline: str,
                stage_ledger_authority: str, stage_ledger_revision: int,
                ownership_held: bool = False) -> dict:
        if type(request) is not ActivationRequestV2:
            return self._result(None, "refused", "artifact_invalid")
        target = request.proof_set["target"]["target_identity"]
        try:
            terminal = self.repository.lookup_terminal_v2(
                target, request_id=request.request_id, request_digest=request.request_digest)
        except Exception:
            return self._result(request, "refused", "request_conflict")
        if terminal is not None:
            return terminal
        owner = nullcontext() if ownership_held else self.repository.operation_transaction(target)
        with owner:
            return self._execute_owned(request, rollback_grant, compose_files,
                                       compose_project, edge_route_digest,
                                       admission_deadline, stage_ledger_authority,
                                       stage_ledger_revision)

    def _execute_owned(self, request, grant_value, compose_files, compose_project,
                       edge_route_digest, admission_deadline,
                       stage_ledger_authority, stage_ledger_revision):
        target = request.proof_set["target"]
        target_key = target["target_identity"]
        effect_entered = False
        transaction_digest = request.request_digest
        try:
            state = self.repository.snapshot(target_key)
            if state.get("generation") != request.expected_generation:
                raise ActivationContractError("generation_conflict")
            current = state.get("current")
            previous = state.get("previous")
            chosen = None
            if request.operation == "rollback":
                if not isinstance(previous, dict):
                    raise ActivationContractError("rollback_unavailable")
                chosen = validate_activation_generation(previous)
                if type(chosen) is not VerifiedActivationGenerationV2:
                    # A v1 rollback must be dispatched to the v1 service.  It is
                    # deliberately not converted into a v2 generation.
                    raise ActivationContractError("rollback_unavailable")
            prior_digest = ((chosen.generation_digest if chosen is not None else
                            (current or {}).get("generation_digest")) or activation_digest(
                "sandbox.hosting.images.activation-genesis.v2",
                {"target": target, "generation": request.expected_generation}))
            grant = (grant_value if type(grant_value) is RollbackCompatibilityGrantV2
                     else RollbackCompatibilityGrantV2.from_mapping(grant_value))
            self._validate_grant(request, grant, prior_digest, chosen)
            status, transaction = self.repository.accept_v2(
                request, proof_set_digest=request.proof_set["proof_digest"],
                recovery_context={"target": target, "compose_project": compose_project,
                                  "selected_services": list(
                                      request.compose_snapshot.selected_services)},
                prior_generation_digest=prior_digest,
                admission_deadline=admission_deadline,
                stage_ledger_authority=stage_ledger_authority,
                stage_ledger_revision=stage_ledger_revision)
            if status == "replay":
                return transaction
            if status == "resume":
                # The retained transaction, not this caller, owns the next
                # decision.  Never repeat a possibly-entered Compose or edge
                # effect.  Recovery must classify the durable state.
                return self._result(
                    request, "uncertain", "effect_unknown",
                    transaction_digest=(transaction or {}).get(
                        "transaction_digest", request.request_digest))
            if status != "accepted" or type(transaction) is not dict:
                raise ActivationContractError({
                    "busy": "target_busy", "generation_conflict": "generation_conflict",
                    "conflict": "request_conflict", "lease_conflict": "lease_conflict",
                }.get(status, "artifact_invalid"))
            transaction_digest = transaction.get("transaction_digest", request.request_digest)
            if chosen is None:
                images, bindings, topology = self._preflight_candidate(
                    request, compose_files, compose_project)
                snapshot_digest = request.compose_snapshot.snapshot_digest
                configuration_digest = request.compose_snapshot.configuration_digest
                plan_set_digest = request.plan_set.plan_set_digest
                proof_set_digest = request.proof_set["proof_digest"]
                policy_digest = request.policy_digest
                topology_digest = request.proof_set["observation"]["observation_digest"]
            else:
                images, bindings, topology = self._preflight_rollback(
                    request, chosen, compose_files, compose_project)
                snapshot_digest = request.compose_snapshot.snapshot_digest
                configuration_digest = chosen.configuration_digest
                plan_set_digest = chosen.plan_set_digest
                proof_set_digest = chosen.proof_set_digest
                policy_digest = chosen.policy_digest
                topology_digest = chosen.topology_digest
            rendered, service_images, environment = topology
            compose_projection = tuple({"service": name, **rendered["services"][name]}
                                       for name in sorted(rendered["services"]))
            replacement_intent = ReplacementIntentV2.create(
                request_digest=request.request_digest,
                generation=request.expected_generation + 1,
                plan_set_digest=plan_set_digest, proof_set_digest=proof_set_digest,
                policy_digest=policy_digest, prior_generation_digest=prior_digest,
                target=target, compose_project=compose_project,
                topology_digest=topology_digest,
                configuration_digest=configuration_digest,
                compose_snapshot=request.compose_snapshot.as_mapping(),
                images=images, service_image_bindings=bindings,
                compose_projection=compose_projection, route_digest=edge_route_digest)
            self.repository.transition_v2(target_key, request, "runtime_pending",
                                          effect_entered=True,
                                          replacement_intent=replacement_intent.as_mapping())
            effect_entered = True
            services = tuple(item["service"] for item in bindings)
            self.runtime_adapter.replace_services_v2(
                compose_files=compose_files, project_name=compose_project,
                services=services, service_image_bindings=service_images,
                environment_bindings=environment, snapshot_digest=snapshot_digest,
                timeout_seconds=300)
            observation = self.runtime_observer.observe(
                target=target, compose_project=compose_project, bindings=bindings,
                images=images, topology_digest=topology_digest,
                compose_config_hashes={name: row["compose_config_hash"]
                    for name, row in rendered["services"].items()},
                edge_identity=edge_route_digest, snapshot_digest=snapshot_digest)
            base = {"schema_version": 2, "generation": request.expected_generation + 1,
                    "plan_set_digest": plan_set_digest, "proof_set_digest": proof_set_digest,
                    "policy_digest": policy_digest, "request_digest": request.request_digest,
                    "target": target,
                    "topology_digest": topology_digest,
                    "configuration_digest": configuration_digest,
                    "compose_snapshot_digest": snapshot_digest,
                    "compose_project": compose_project, "images": images,
                    "service_image_bindings": bindings,
                    "compose_projection": compose_projection,
                    "service_projection": tuple(observation["services"]),
                    "running_observation_digest": observation["observation_digest"],
                    "rollback_from_generation_digest": prior_digest}
            subject_digest = activation_digest(
                "sandbox.hosting.images.activation-generation-subject.v2",
                {key: (list(value) if isinstance(value, tuple) else value)
                 for key, value in base.items()})
            self.repository.transition_v2(
                target_key, request, "runtime_proven",
                running_observation=observation, generation_subject={
                    **{key: (list(value) if isinstance(value, tuple) else value)
                       for key, value in base.items()},
                    "generation_subject_digest": subject_digest})
            edge_prepared = {"schema_version": 2, "phase": "prepared",
                "request_digest": request.request_digest,
                "generation": request.expected_generation + 1,
                "generation_subject_digest": subject_digest,
                "route_digest": edge_route_digest,
                "observation_digest": observation["observation_digest"],
                "terminal": False, "receipt_digest": None}
            self.repository.transition_v2(
                target_key, request, "edge_pending", edge_result=edge_prepared)
            receipt_mapping = self.edge_adapter.apply_generation_v2(
                request_digest=request.request_digest, target=target,
                generation=request.expected_generation + 1,
                generation_subject_digest=subject_digest,
                route_digest=edge_route_digest,
                observation_digest=observation["observation_digest"])
            receipt = GenerationBoundEdgeReceiptV2.from_mapping(receipt_mapping)
            body = {**base, "edge_receipt": receipt.as_mapping()}
            generation = VerifiedActivationGenerationV2(
                **body, generation_digest=activation_digest(
                    "sandbox.hosting.images.activation-generation.v2",
                    {key: (list(value) if isinstance(value, tuple) else value)
                     for key, value in body.items()}))
            self.repository.transition_v2(
                target_key, request, "edge_pending",
                edge_result=receipt.as_mapping(),
                candidate_generation=generation.as_mapping())
            durable = self.edge_adapter.observe_generation_v2(
                request_digest=request.request_digest, target=target,
                generation=request.expected_generation + 1,
                generation_subject_digest=subject_digest,
                route_digest=edge_route_digest,
                observation_digest=observation["observation_digest"])
            if GenerationBoundEdgeReceiptV2.from_mapping(durable) != receipt:
                raise ActivationContractError("edge_uncertain")
            post_edge = self.runtime_observer.observe(
                target=target, compose_project=compose_project, bindings=bindings,
                images=images, topology_digest=topology_digest,
                compose_config_hashes={name: row["compose_config_hash"]
                    for name, row in rendered["services"].items()},
                edge_identity=edge_route_digest, snapshot_digest=snapshot_digest)
            if post_edge != observation:
                raise ActivationContractError("runtime_mismatch")
            result = self._result(request, "success", "committed",
                                  generation=generation,
                                  transaction_digest=transaction_digest)
            self.repository.commit_v2(target_key, request, result,
                                      generation.as_mapping())
            return result
        except ActivationContractError as exc:
            result_class = "uncertain" if effect_entered else "refused"
            result = self._result(request, result_class, exc.code,
                                  transaction_digest=transaction_digest)
        except Exception:
            result_class = "uncertain" if effect_entered else "refused"
            result = self._result(request, result_class,
                                  "effect_unknown" if effect_entered else "artifact_invalid",
                                  transaction_digest=transaction_digest)
        try:
            self.repository.fence_v2(target_key, request, result,
                                     effect_entered=effect_entered)
        except Exception:
            return self._result(request, "uncertain", "effect_unknown")
        return result

    def _validate_grant(self, request, grant, prior_digest, retained):
        now = int(self.clock())
        candidate_plan = (retained.plan_set_digest if retained is not None
                          else request.plan_set.plan_set_digest)
        candidate_proof = (retained.proof_set_digest if retained is not None
                           else request.proof_set["proof_digest"])
        candidate_policy = retained.policy_digest if retained is not None else request.policy_digest
        if ((retained is not None and (
                    request.plan_set.plan_set_digest != retained.plan_set_digest
                    or request.proof_set["proof_digest"] != retained.proof_set_digest
                    or request.policy_digest != retained.policy_digest))
                or request.rollback_grant_digest != grant.grant_digest
                or grant.target != request.proof_set["target"]
                or grant.expected_generation != request.expected_generation
                or grant.prior_generation_digest != prior_digest
                or grant.candidate_plan_set_digest != candidate_plan
                or grant.candidate_proof_set_digest != candidate_proof
                or grant.policy_digest != candidate_policy
                or not grant.issued_at <= now < grant.expires_at
                or self.rollback_grant_verifier.verify(grant) is not True):
            raise ActivationContractError("rollback_grant_mismatch")

    def _preflight_candidate(self, request, compose_files, compose_project):
        if request.policy_digest != request.plan_set.policy.policy_digest \
                or int(self.clock()) >= request.compose_snapshot.expires_at:
            raise ActivationContractError("policy_mismatch")
        images = self.runtime_observer.prove_all_local(request)
        bindings = plan_set_bindings(request.plan_set)
        service_images = {item["service"]: item["image_ref"] for item in bindings}
        by_name = {item.name: item.image_ref for item in request.plan_set.receipt.images}
        environment = {variable: by_name[name]
                       for name, variable in request.plan_set.policy.activation_environment_bindings}
        rendered = self.runtime_adapter.render_topology_v2(
            compose_files=compose_files, project_name=compose_project,
            selected_services=tuple(item["service"] for item in bindings),
            service_image_bindings=service_images, environment_bindings=environment,
            topology_digest=request.proof_set["observation"]["observation_digest"],
            private_compose_snapshot=request.compose_snapshot.as_mapping())
        validate_rendered_topology_v2(rendered, request=request)
        return images, bindings, (rendered, service_images, environment)

    def _preflight_rollback(self, request, previous, compose_files, compose_project):
        if previous.target != request.proof_set["target"] \
                or previous.compose_project != compose_project \
                or previous.configuration_digest != request.compose_snapshot.configuration_digest:
            raise ActivationContractError("rollback_grant_mismatch")
        self.runtime_observer.prove_generation_local(previous)
        bindings = previous.service_image_bindings
        images = previous.images
        service_images = {item["service"]: item["image_ref"] for item in bindings}
        environment = {item["environment_variable"]: item["image_ref"] for item in bindings}
        rendered = self.runtime_adapter.render_topology_v2(
            compose_files=compose_files, project_name=compose_project,
            selected_services=tuple(item["service"] for item in bindings),
            service_image_bindings=service_images, environment_bindings=environment,
            topology_digest=previous.topology_digest,
            private_compose_snapshot=request.compose_snapshot.as_mapping())
        if tuple({"service": name, **rendered["services"][name]}
                 for name in sorted(rendered["services"])) != previous.compose_projection:
            raise ActivationContractError("topology_mismatch")
        return images, bindings, (rendered, service_images, environment)

    @staticmethod
    def _result(request, result_class, code, *, generation=None,
                transaction_digest=None):
        return {"schema_version": 2, "ok": result_class == "success",
                "result_class": result_class, "code": code,
                "request_id": getattr(request, "request_id", "invalid"),
                "request_digest": getattr(request, "request_digest", "sha256:" + "0" * 64),
                "transaction_digest": transaction_digest or getattr(
                    request, "request_digest", "sha256:" + "0" * 64),
                "starting_generation": getattr(request, "expected_generation", 0),
                "resulting_generation": (generation.generation if generation is not None
                                         else getattr(request, "expected_generation", 0)),
                "generation_digest": (generation.generation_digest
                                      if generation is not None else None)}


__all__ = ("ActivationServiceV2",)
