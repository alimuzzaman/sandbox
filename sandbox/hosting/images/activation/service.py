"""One fenced state machine for activate, adopt, and rollback."""

from __future__ import annotations

import time

from .init_runner import InitExecutionUncertain, InitRunner
from .models import (
    ActivationContractError, ActivationResult, VerifiedActivationGeneration,
    activation_digest,
)
from .policy import admit_activation, validate_rollback_grant
from .runtime_observer import validate_rendered_topology


class ActivationService:
    def __init__(self, *, repository, runtime_adapter, runtime_observer,
                 edge_adapter, clock=None) -> None:
        self.repository = repository
        self.runtime_adapter = runtime_adapter
        self.runtime_observer = runtime_observer
        self.edge_adapter = edge_adapter
        self.clock = clock or time.time

    def execute(self, request, policy, binding, *, rollback_subject, rollback_grant,
                admission_deadline: str, compose_files: tuple[str, ...],
                compose_project: str, configuration_digest: str,
                init_data_contract_digest: str, edge_required: bool = True) -> dict:
        target = request.proof.target.target_identity
        try:
            terminal = self.repository.lookup_terminal(
                target, request_id=request.request_id, request_digest=request.request_digest)
        except Exception:
            return self._unaccepted(request, "request_conflict")
        if terminal is not None:
            return terminal
        admission = admit_activation(request, policy, binding, capability=request.operation)
        if not admission.ok:
            return self._unaccepted(request, admission.code)
        if edge_required is not policy.edge_required:
            return self._unaccepted(request, "authority_mismatch")
        accepted_at = int(self.clock())
        try:
            if request.operation == "rollback" \
                    and request.rollback_grant_digest != rollback_grant.grant_digest:
                raise ActivationContractError("rollback_grant_mismatch")
            validate_rollback_grant(
                rollback_grant, rollback_subject, accepted_at=accepted_at,
                policy_revision=policy.authority_revision, now=accepted_at)
        except ActivationContractError as exc:
            return self._unaccepted(request, exc.code)
        with self.repository.operation_transaction(target):
            return self._execute_owned(
                request, policy, binding, rollback_subject, rollback_grant,
                admission_deadline=admission_deadline, compose_files=compose_files,
                compose_project=compose_project, configuration_digest=configuration_digest,
                init_data_contract_digest=init_data_contract_digest,
                edge_required=edge_required, target=target)

    def _execute_owned(self, request, policy, binding, rollback_subject, rollback_grant,
                       *, admission_deadline, compose_files, compose_project,
                       configuration_digest, init_data_contract_digest,
                       edge_required, target):
        state_before = self.repository.snapshot(target)
        current = state_before.get("current")
        rollback_target = ((current or {}).get("generation_digest") or activation_digest(
            "sandbox.hosting.images.activation-genesis.v1",
            {"target": request.proof.target.as_mapping(), "generation": request.expected_generation}))
        expected_plan = request.plan.plan_digest if request.operation != "rollback" else (current or {}).get("plan_digest")
        expected_proof = request.proof.proof_digest if request.operation != "rollback" else (current or {}).get("proof_digest")
        expected_config = configuration_digest if request.operation != "rollback" else (current or {}).get("configuration_digest")
        expected_topology = request.proof.observed_identity["topology_digest"] if request.operation != "rollback" else (current or {}).get("topology_digest")
        if rollback_subject.target != request.proof.target.as_mapping() \
                or rollback_subject.rollback_target_generation_digest != rollback_target \
                or rollback_subject.candidate_plan_digest != expected_plan \
                or rollback_subject.candidate_proof_digest != expected_proof \
                or rollback_subject.activation_authority_digest != binding.binding_digest \
                or rollback_subject.configuration_digest != expected_config \
                or rollback_subject.topology_digest != expected_topology \
                or rollback_subject.init_data_contract_digest != init_data_contract_digest \
                or rollback_subject.policy_revision != policy.authority_revision:
            return self._unaccepted(request, "rollback_grant_mismatch")
        status, replay, lease = self.repository.accept(
            request, authority_binding_digest=binding.binding_digest,
            rollback_subject_digest=rollback_subject.subject_digest,
            rollback_grant_digest=rollback_grant.grant_digest,
            admission_deadline=admission_deadline, edge_required=edge_required)
        if status == "replay" and replay is not None:
            if replay.get("result_class") != "uncertain" and lease is not None:
                self.repository.release_terminal_pin(
                    lease, terminal_receipt=replay["transaction_digest"])
            return replay
        if status not in {"accepted", "resume"} or lease is None:
            return self._unaccepted(request, {
                "generation_conflict": "generation_conflict", "busy": "target_busy",
                "conflict": "request_conflict"}.get(status, "lease_conflict"))
        if status == "resume":
            active = self.repository.snapshot(target).get("active")
            if not isinstance(active, dict) or active.get("request_digest") != request.request_digest:
                return self._unaccepted(request, "request_conflict")
            if active.get("phase") != "accepted":
                return ActivationResult(
                    1, False, "in_progress", "accepted", request.operation,
                    request.request_id, request.request_digest, request.expected_generation,
                    request.expected_generation, active["transaction_digest"]).as_mapping()
        try:
            if request.operation == "adopt":
                return self._adopt(
                    request, policy, rollback_subject, rollback_grant, lease,
                    compose_files, compose_project, configuration_digest,
                    init_data_contract_digest, edge_required)
            if request.operation == "rollback":
                return self._rollback(
                    request, policy, rollback_subject, rollback_grant, lease,
                    compose_files, compose_project, configuration_digest,
                    init_data_contract_digest, edge_required)
            return self._activate(
                request, policy, rollback_subject, rollback_grant, lease,
                compose_files, compose_project, configuration_digest,
                init_data_contract_digest, edge_required)
        except InitExecutionUncertain:
            return self._fence(request, target, "init_uncertain", lease=lease)
        except ActivationContractError as exc:
            return self._fence(request, target, exc.code, refused=True, lease=lease)
        except TimeoutError:
            return self._fence(request, target, "effect_unknown", lease=lease)
        except Exception:
            return self._fence(request, target, "effect_unknown", lease=lease)

    def _preflight(self, request, policy, *, compose_files, compose_project,
                   configuration_digest):
        target = request.proof.target.as_mapping()
        proof = request.proof
        self.runtime_observer.prove_local(target=target, proof=proof)
        image = request.plan.image.repository_qualified_digest
        rendered = self.runtime_adapter.render_topology(
            compose_files=compose_files, project_name=compose_project,
            selected_services=policy.selected_services,
            image_overrides={service: image for service in policy.selected_services})
        validate_rendered_topology(
            rendered, selected_services=policy.selected_services,
            exact_image=image, exact_platform=request.plan.image.platform.as_mapping(),
            exact_topology_digest=request.proof.observed_identity["topology_digest"],
            exact_service_projection=policy.compose_projection)
        return target, image, rendered

    def _activate(self, request, policy, subject, grant, lease, compose_files,
                  compose_project, configuration_digest, init_data_contract_digest,
                  edge_required):
        target_key = request.proof.target.target_identity
        self.repository.transition(target_key, request, "preflight")
        target, image, rendered = self._preflight(
            request, policy, compose_files=compose_files, compose_project=compose_project,
            configuration_digest=configuration_digest)
        receipts = []
        if policy.init_declarations:
            self.repository.transition(target_key, request, "init_pending", effect_entered=False)
            for index, declaration in enumerate(policy.init_declarations):
                declaration_digest = activation_digest(
                    "sandbox.hosting.images.init-declaration.v1", declaration)
                self.repository.transition(target_key, request, "init_pending", init_step={
                    "index": index, "declaration_digest": declaration_digest,
                    "inspection_digest": None, "effect_entered": False,
                    "receipt_digest": None})
                def persist_effect(declaration_digest, inspection_digest):
                    self.repository.transition(
                        target_key, request, "init_pending", effect_entered=True,
                        init_step={"index": index,
                            "declaration_digest": declaration_digest,
                            "inspection_digest": inspection_digest,
                            "effect_entered": True, "receipt_digest": None})
                runner = InitRunner(self.runtime_adapter, persist_effect_entered=persist_effect)
                receipt = runner.run(
                    declaration, exact_image=image,
                    local_image_id=request.plan.image.config_digest,
                    platform=request.plan.image.platform.as_mapping(), target=target,
                    runtime_epoch=rendered["runtime_epoch"])
                receipts.append(receipt)
                self.repository.transition(
                    target_key, request, "init_pending", init_receipt=receipt.as_mapping(),
                    init_step={"index": index, "declaration_digest": declaration_digest,
                        "inspection_digest": receipt.inspection_digest,
                        "effect_entered": True, "receipt_digest": receipt.receipt_digest})
        self.repository.transition(target_key, request, "runtime_pending", effect_entered=True)
        self.runtime_adapter.replace_services(
            compose_files=compose_files, project_name=compose_project,
            services=policy.selected_services, exact_image=image,
            environment_overrides={
                f"SANDBOX_ACTIVATION_IMAGE_{service.upper().replace('-', '_')}": image
                for service in policy.selected_services}, timeout_seconds=300)
        observation = self._observe(request, policy, target, image, rendered)
        generation = self._generation(
            request, policy, subject, grant, observation, receipts,
            configuration_digest, edge_receipt_digest=None)
        self.repository.transition(
            target_key, request, "runtime_proven",
            running_observation=observation.as_mapping(),
            candidate_generation=generation.as_mapping())
        if edge_required:
            self._persist_edge_pending(request, policy, target_key, observation)
        edge = self._edge(request, policy, observation, required=edge_required, allow_effect=True)
        if edge is not None:
            generation = self._generation(
                request, policy, subject, grant, observation, receipts,
                configuration_digest, edge_receipt_digest=edge["receipt_digest"])
            self.repository.transition(
                target_key, request, "edge_pending", edge_result=edge,
                candidate_generation=generation.as_mapping())
        return self._commit_success(request, target_key, generation, observation, lease)

    def _adopt(self, request, policy, subject, grant, lease, compose_files,
               compose_project, configuration_digest, init_data_contract_digest,
               edge_required):
        if policy.init_declarations:
            raise ActivationContractError("adoption_requires_zero_init")
        target_key = request.proof.target.target_identity
        self.repository.transition(target_key, request, "preflight")
        target, image, rendered = self._preflight(
            request, policy, compose_files=compose_files, compose_project=compose_project,
            configuration_digest=configuration_digest)
        observation = self._observe(request, policy, target, image, rendered)
        edge = self._edge(request, policy, observation, required=edge_required, allow_effect=False)
        generation = self._generation(
            request, policy, subject, grant, observation, (), configuration_digest,
            edge_receipt_digest=(edge or {}).get("receipt_digest"))
        self.repository.transition(
            target_key, request, "runtime_proven",
            running_observation=observation.as_mapping(),
            candidate_generation=generation.as_mapping())
        return self._commit_success(request, target_key, generation, observation, lease)

    def _rollback(self, request, policy, subject, grant, lease, compose_files,
                  compose_project, configuration_digest, init_data_contract_digest,
                  edge_required):
        target_key = request.proof.target.target_identity
        state = self.repository.snapshot(target_key)
        previous = state.get("previous")
        current = state.get("current")
        if not isinstance(previous, dict) or not isinstance(current, dict) \
                or current.get("rollback_subject_digest") != subject.subject_digest \
                or current.get("rollback_grant_digest") != grant.grant_digest \
                or previous.get("generation_digest") != subject.rollback_target_generation_digest:
            raise ActivationContractError("rollback_unavailable")
        if previous.get("configuration_digest") != configuration_digest:
            raise ActivationContractError("rollback_grant_mismatch")
        self.repository.transition(target_key, request, "preflight")
        exact_image = previous["image"]["repository_qualified_digest"]
        local = self.runtime_adapter.observe_local_image(
            target=request.proof.target.as_mapping(), repository_digest=exact_image)
        if type(local) is not dict or local.get("repo_digest") != exact_image:
            raise ActivationContractError("local_image_mismatch")
        self.repository.transition(target_key, request, "runtime_pending", effect_entered=True)
        self.runtime_adapter.replace_services(
            compose_files=compose_files, project_name=compose_project,
            services=policy.selected_services, exact_image=exact_image,
            environment_overrides={
                f"SANDBOX_ACTIVATION_IMAGE_{service.upper().replace('-', '_')}": exact_image
                for service in policy.selected_services}, timeout_seconds=300)
        observation = self.runtime_observer.observe(
            target=request.proof.target.as_mapping(), selected_services=policy.selected_services,
            exact_image=exact_image, local_image_id=previous["image"]["config_digest"],
            config_digest=previous["image"]["config_digest"],
            platform=previous["image"]["platform"],
            topology_digest=previous["topology_digest"], edge_identity=policy.edge_policy_digest)
        generation = self._generation_from_previous(request, previous, observation, grant, subject)
        self.repository.transition(
            target_key, request, "runtime_proven",
            running_observation=observation.as_mapping(),
            candidate_generation=generation.as_mapping())
        if edge_required:
            self._persist_edge_pending(request, policy, target_key, observation)
        edge = self._edge(request, policy, observation, required=edge_required, allow_effect=True)
        if edge is not None:
            mapping = generation.body_mapping(); mapping["edge_receipt_digest"] = edge["receipt_digest"]
            generation = VerifiedActivationGeneration(
                **mapping, generation_digest=activation_digest(
                    "sandbox.hosting.images.activation-generation.v1", mapping))
            self.repository.transition(target_key, request, "edge_pending", edge_result=edge,
                                       candidate_generation=generation.as_mapping())
        return self._commit_success(request, target_key, generation, observation, lease)

    def _observe(self, request, policy, target, image, rendered):
        return self.runtime_observer.observe(
            target=target, selected_services=policy.selected_services,
            exact_image=image, local_image_id=request.plan.image.config_digest,
            config_digest=request.plan.image.config_digest,
            platform=request.plan.image.platform.as_mapping(),
            topology_digest=request.proof.observed_identity["topology_digest"],
            edge_identity=policy.edge_policy_digest)

    def _edge(self, request, policy, observation, *, required, allow_effect):
        if not required:
            return None
        if self.edge_adapter.observe_plan() != {
                "routes": list(policy.edge_route_plan),
                "route_digest": policy.edge_route_digest}:
            raise ActivationContractError("edge_incomplete")
        edge_request_id = f"{request.request_id}/edge"
        edge_digest = activation_digest("sandbox.hosting.images.activation-edge.v1", {
            "request_id": edge_request_id, "transaction_request": request.request_digest,
            "observation": observation.observation_digest,
            "route_digest": policy.edge_route_digest})
        existing = self.edge_adapter.lookup(edge_request_id, edge_digest)
        if isinstance(existing, dict) and existing.get("terminal") is True \
                and existing.get("request_digest") == edge_digest:
            return existing
        if existing is not None and existing != "not_entered":
            raise ActivationContractError("edge_uncertain")
        if not allow_effect:
            body = {"request_id": edge_request_id, "request_digest": edge_digest,
                    "terminal": True, "observed_only": True,
                    "route_digest": policy.edge_route_digest}
            return {**body, "receipt_digest": activation_digest(
                "sandbox.hosting.images.activation-edge-observation.v1", body)}
        result = self.edge_adapter.apply(edge_request_id, edge_digest)
        if type(result) is not dict or result.get("terminal") is not True \
                or result.get("request_digest") != edge_digest:
            raise ActivationContractError("edge_uncertain")
        return result

    def _persist_edge_pending(self, request, policy, target, observation):
        edge_request_id = f"{request.request_id}/edge"
        edge_digest = activation_digest("sandbox.hosting.images.activation-edge.v1", {
            "request_id": edge_request_id, "transaction_request": request.request_digest,
            "observation": observation.observation_digest,
            "route_digest": policy.edge_route_digest})
        self.repository.transition(target, request, "edge_pending", edge_result={
            "request_id": edge_request_id, "request_digest": edge_digest,
            "phase": "prepared", "terminal": False, "receipt_digest": None})

    def _generation(self, request, policy, subject, grant, observation, receipts,
                    configuration_digest, edge_receipt_digest):
        state = self.repository.snapshot(request.proof.target.target_identity)
        active = state["active"]
        body = {"generation": request.expected_generation + 1,
                "plan_digest": request.plan.plan_digest,
                "proof_digest": request.proof.proof_digest,
                "policy_digest": policy.policy_digest,
                "request_digest": request.request_digest,
                "transaction_digest": active["transaction_digest"],
                "target": request.proof.target.as_mapping(),
                "image": {**request.plan.image.as_mapping(),
                          "repository_qualified_digest": request.plan.image.repository_qualified_digest},
                "topology_digest": request.proof.observed_identity["topology_digest"],
                "configuration_digest": configuration_digest,
                "init_receipt_digests": tuple(item.receipt_digest for item in receipts),
                "running_observation_digest": observation.observation_digest,
                "service_projection": observation.services,
                "edge_receipt_digest": edge_receipt_digest or activation_digest(
                    "sandbox.hosting.images.no-edge.v1", {"request": request.request_digest}),
                "proof_pin_digest": activation_digest(
                    "sandbox.hosting.images.activation-proof-pin.v1", active["proof_pin"]),
                "rollback_subject_digest": subject.subject_digest,
                "rollback_grant_digest": grant.grant_digest}
        return VerifiedActivationGeneration(**body, generation_digest=activation_digest(
            "sandbox.hosting.images.activation-generation.v1",
            {**body, "init_receipt_digests": list(body["init_receipt_digests"])}))

    def _generation_from_previous(self, request, previous, observation, grant, subject):
        state = self.repository.snapshot(request.proof.target.target_identity)
        body = {**previous, "generation": request.expected_generation + 1,
                "request_digest": request.request_digest,
                "transaction_digest": state["active"]["transaction_digest"],
                "running_observation_digest": observation.observation_digest,
                "rollback_subject_digest": subject.subject_digest,
                "rollback_grant_digest": grant.grant_digest}
        body.pop("generation_digest", None)
        body["init_receipt_digests"] = tuple(body["init_receipt_digests"])
        body["service_projection"] = tuple(body["service_projection"])
        return VerifiedActivationGeneration(**body, generation_digest=activation_digest(
            "sandbox.hosting.images.activation-generation.v1",
            {**body, "init_receipt_digests": list(body["init_receipt_digests"]),
             "service_projection": list(body["service_projection"])}))

    def _commit_success(self, request, target, generation, observation, lease):
        state = self.repository.snapshot(target); transaction = state["active"]
        result = ActivationResult(
            1, True, "success", "committed", request.operation,
            request.request_id, request.request_digest, request.expected_generation,
            request.expected_generation + 1, transaction["transaction_digest"],
            generation.generation_digest, observation.observation_digest)
        self.repository.commit(target, request, result, generation.as_mapping())
        self.repository.release_terminal_pin(
            lease, terminal_receipt=transaction["transaction_digest"])
        return result.as_mapping()

    def _fence(self, request, target, code, *, refused=False, lease=None):
        try:
            state = self.repository.snapshot(target); active = state.get("active")
            transaction = active["transaction_digest"] if isinstance(active, dict) else request.request_digest
            result = ActivationResult(
                1, False, "refused" if refused else "uncertain", code,
                request.operation, request.request_id, request.request_digest,
                request.expected_generation, request.expected_generation, transaction)
            self.repository.commit(target, request, result)
            if refused and lease is not None:
                self.repository.release_terminal_pin(
                    lease, terminal_receipt=transaction)
            return result.as_mapping()
        except Exception:
            return self._unaccepted(request, "effect_unknown", result_class="uncertain")

    @staticmethod
    def _unaccepted(request, code, *, result_class="refused"):
        return ActivationResult(
            1, False, result_class, code, request.operation, request.request_id,
            request.request_digest, request.expected_generation,
            request.expected_generation, request.request_digest).as_mapping()
