"""Inspectable one-shot init runner with a durable pre-start boundary."""

from __future__ import annotations

from .models import (
    MAX_OUTPUT_BYTES, ActivationContractError, InitReceipt, activation_digest,
)


class InitExecutionUncertain(RuntimeError):
    pass


class InitRunner:
    def __init__(self, adapter, *, persist_effect_entered) -> None:
        self.adapter = adapter
        self.persist_effect_entered = persist_effect_entered

    def run(self, declaration: dict, *, exact_image: str, local_image_id: str,
            platform: dict, target: dict, runtime_epoch: str) -> InitReceipt:
        created = self.adapter.create_init(
            declaration=declaration, image=exact_image, platform=platform,
            target=target, start=False)
        started = False
        try:
            inspection = self.adapter.inspect_init(created)
            expected = {
                "image": exact_image, "local_image_id": local_image_id,
                "platform": platform, "command": declaration["command"],
                "mounts": declaration["mounts"], "networks": declaration["networks"],
                "environment_keys": declaration["environment_keys"],
                "privileged": declaration["privileged"],
                "dependencies": declaration["dependencies"], "target": target,
                "runtime_epoch": runtime_epoch,
            }
            if type(inspection) is not dict or inspection != expected:
                removed = self.adapter.remove_init(created, force=False)
                if removed is not True:
                    raise InitExecutionUncertain("pre-start cleanup is unproven")
                raise ActivationContractError("init_mismatch")
            declaration_digest = activation_digest(
                "sandbox.hosting.images.init-declaration.v1", declaration)
            inspection_digest = activation_digest(
                "sandbox.hosting.images.init-inspection.v1", inspection)
            # This must fsync through the shared state owner before start.
            self.persist_effect_entered(declaration_digest, inspection_digest)
            started = True
            self.adapter.start_init(created)
            result = self.adapter.wait_init(
                created, timeout_seconds=declaration["timeout_seconds"],
                max_output_bytes=MAX_OUTPUT_BYTES)
            if type(result) is not dict or set(result) != {
                    "exit_code", "terminated", "output_bytes", "cancelled"} \
                    or type(result["output_bytes"]) is not int \
                    or result["output_bytes"] > MAX_OUTPUT_BYTES \
                    or result["exit_code"] != 0 or result["terminated"] is not True \
                    or result["cancelled"] is not False:
                raise InitExecutionUncertain("init termination is unproven")
            cleanup = self.adapter.remove_init(created, force=False)
            if cleanup is not True:
                raise InitExecutionUncertain("init cleanup is unproven")
            body = {"declaration_digest": declaration_digest,
                    "target_epoch": target["machine_identity"],
                    "runtime_epoch": runtime_epoch, "local_image_id": local_image_id,
                    "created_identity": created.identity,
                    "inspection_digest": inspection_digest, "effect_entered": True,
                    "exit_code": 0, "termination_complete": True,
                    "cleanup_complete": True}
            return InitReceipt(**body, receipt_digest=activation_digest(
                "sandbox.hosting.images.init-receipt.v1", body))
        except ActivationContractError:
            raise
        except Exception as exc:
            if started:
                try:
                    cancelled = self.adapter.cancel_init(created)
                    terminated = self.adapter.wait_terminated(
                        created, timeout_seconds=declaration["timeout_seconds"])
                    removed = self.adapter.remove_init(created, force=True)
                except Exception:
                    cancelled = terminated = removed = False
                if cancelled is not True or terminated is not True or removed is not True:
                    raise InitExecutionUncertain("possible init execution remains fenced") from None
                raise InitExecutionUncertain("init entered without terminal receipt") from None
            try: self.adapter.remove_init(created, force=False)
            except Exception: pass
            if isinstance(exc, InitExecutionUncertain):
                raise
            raise ActivationContractError("init_mismatch") from None
