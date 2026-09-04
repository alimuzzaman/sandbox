from __future__ import annotations

import time
from typing import Sequence
import datetime
import hashlib
import uuid

from sandbox.server_config.models import (
    ServerConfigFragment, FragmentSet, OperationResult, TerminalOutcome,
    ActivationTransaction, Operation, TransactionPhase, PhaseEvidence,
    KnownGoodReceipt, RuntimeObservation, ServerType
)
from sandbox.server_config.policy import validate_fragment_name, validate_fragment_bytes, AUTHORITY
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.adapters.base import ServerConfigAdapter, RenderedGeneration
from sandbox.server_config.context import Clock


class ServerConfigService:
    def __init__(
        self,
        *,
        repository: ServerConfigRepository,
        adapter: ServerConfigAdapter,
        clock: Clock,
        instance_authority: object = None,
    ) -> None:
        self.repository = repository
        self.adapter = adapter
        self.clock = clock
        self.instance_authority = instance_authority
        
    def _read_fragments(self) -> list[ServerConfigFragment]:
        state = self.repository.read_state()
        if not state or not isinstance(state, dict):
            return []
        
        frags = []
        for item in state.get("fragments", []):
            frag = ServerConfigFragment(
                name=item["name"],
                authority=item["authority"],
                server_type=ServerType(item.get("server_type", self.adapter.descriptor.server_type)),
                content_id=item["content_id"],
                content_size=item["content_size"],
                content_locator=item["content_locator"],
                instance_incarnation_id=item["instance_incarnation_id"],
                created_at=datetime.datetime.fromisoformat(item["created_at"]),
                activated_at=datetime.datetime.fromisoformat(item["activated_at"]) if item.get("activated_at") else None,
                policy_revision=item["policy_revision"]
            )
            frags.append(frag)
        return frags

    def apply(
        self, 
        fragment: ServerConfigFragment | None = None, 
        *, 
        name: str | None = None, 
        content: bytes | None = None, 
        authority: str = "wordpress-cache-v1"
    ) -> OperationResult:
        
        if fragment is not None:
            frag_name = fragment.name
            frag = fragment
        else:
            if name is None or content is None:
                raise ValueError("Must provide fragment or name/content")
            frag_name = name
            validate_fragment_name(frag_name)
            validate_fragment_bytes(content)
            
            locator = "fragments/" + "mock"
            frag = ServerConfigFragment.create(
                name=frag_name,
                authority=authority,
                server_type=ServerType(self.adapter.descriptor.server_type),
                content=content,
                content_locator=locator,
                instance_incarnation_id="inc_00000000000000000000000000000000",
                created_at=self.clock.now(),
                policy_revision="v1"
            )

        existing = self._read_fragments()
        for ex in existing:
            if ex.name == frag.name and ex.content_id == frag.content_id:
                return OperationResult(
                    outcome=TerminalOutcome.NO_OP,
                    code="ok",
                    mutated=False,
                    instance_incarnation_id=None,
                    fragment_name=frag.name,
                    fragment_set_id=None
                )
        
        new_fragments = []
        replaced = False
        for ex in existing:
            if ex.name == frag.name:
                new_fragments.append(frag)
                replaced = True
            else:
                new_fragments.append(ex)
                
        if not replaced:
            new_fragments.append(frag)
            
        new_fragments.sort(key=lambda x: x.name)
        
        generation = self.adapter.render(new_fragments, self.instance_authority)
        
        files = {}
        manifest = {
            "schema": 1,
            "fragment_set_id": "sha256:" + hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            "renderer_revision": "nginx/1"
        }
        
        self.repository.publish_generation(
            files=files,
            manifest=manifest
        )
        
        state_repr = {
            "fragments": [
                {
                    "name": f.name,
                    "authority": f.authority,
                    "server_type": f.server_type.value,
                    "content_id": f.content_id,
                    "content_size": f.content_size,
                    "content_locator": f.content_locator,
                    "instance_incarnation_id": f.instance_incarnation_id,
                    "created_at": f.created_at.isoformat(),
                    "activated_at": f.activated_at.isoformat() if f.activated_at else None,
                    "policy_revision": f.policy_revision
                } for f in new_fragments
            ]
        }
        self.repository.write_state(state_repr)
        
        return OperationResult(
            outcome=TerminalOutcome.ACTIVE,
            code="ok",
            mutated=True,
            instance_incarnation_id=None,
            fragment_name=frag.name,
            fragment_set_id=None
        )

    def list(self) -> tuple[ServerConfigFragment, ...]:
        return tuple(self._read_fragments())

    def show(self, name: str) -> ServerConfigFragment | None:
        for f in self._read_fragments():
            if f.name == name:
                return f
        return None

    def revert(self, name: str) -> OperationResult:
        existing = self._read_fragments()
        if not any(ex.name == name for ex in existing):
            return OperationResult(
                outcome=TerminalOutcome.NO_OP,
                code="ok",
                mutated=False,
                instance_incarnation_id=None,
                fragment_name=name,
                fragment_set_id=None
            )
            
        new_fragments = [ex for ex in existing if ex.name != name]
        new_fragments.sort(key=lambda x: x.name)
        
        state_repr = {
            "fragments": [
                {
                    "name": f.name,
                    "authority": f.authority,
                    "server_type": f.server_type.value,
                    "content_id": f.content_id,
                    "content_size": f.content_size,
                    "content_locator": f.content_locator,
                    "instance_incarnation_id": f.instance_incarnation_id,
                    "created_at": f.created_at.isoformat(),
                    "activated_at": f.activated_at.isoformat() if f.activated_at else None,
                    "policy_revision": f.policy_revision
                } for f in new_fragments
            ]
        }
        self.repository.write_state(state_repr)
        
        return OperationResult(
            outcome=TerminalOutcome.ACTIVE,
            code="ok",
            mutated=True,
            instance_incarnation_id=None,
            fragment_name=name,
            fragment_set_id=None
        )
