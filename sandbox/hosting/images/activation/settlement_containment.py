"""Reviewable containment plans bound to one retained uncertain transaction."""
import re
from .models import activation_digest
from .settlement_service import SettlementError, SettlementService


def containment(args, *, target, repository, observer, store):
    operation = args.settlement_phase
    with repository.operation_transaction(target):
        state = repository.snapshot(target)
        active = SettlementService._active(state, target=target,
            transaction_digest=args.activation_transaction, expected_generation=args.expected_generation)
        if operation == 'containment-plan':
            response = observer.containment(transaction=active, generation=args.expected_generation)
            rows = response.get('containers')
            if not isinstance(rows, list) or len(rows) > 128: raise SettlementError('artifact_invalid')
            body = {'schema_version': 1, 'request_id': args.request_id,
                'transaction_digest': active['transaction_digest'], 'generation': args.expected_generation,
                'target': active['recovery_context']['target'], 'containers': rows,
                'operation': 'disable-restart-and-stop', 'preserve_volumes': True}
            plan = {**body, 'plan_digest': activation_digest('sandbox.hosting.images.containment-plan.v1', body)}
            return {'schema_version': 1, 'ok': True, 'code': 'planned', 'plan': plan}
        from .settlement_cli import read_document
        plan = read_document(getattr(args, 'settlement_plan', None))
        fields = {'schema_version', 'request_id', 'transaction_digest', 'generation', 'target',
                  'containers', 'operation', 'preserve_volumes', 'plan_digest'}
        if (type(plan) is not dict or set(plan) != fields or plan['schema_version'] != 1
                or plan['request_id'] != args.request_id or plan['transaction_digest'] != active['transaction_digest']
                or plan['generation'] != args.expected_generation or plan['target'] != active['recovery_context']['target']
                or plan['operation'] != 'disable-restart-and-stop' or plan['preserve_volumes'] is not True
                or plan['plan_digest'] != activation_digest('sandbox.hosting.images.containment-plan.v1',
                    {key: value for key, value in plan.items() if key != 'plan_digest'})):
            raise SettlementError('evidence_changed')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', args.request_id): raise SettlementError('artifact_invalid')
        from ..provisioning import _read_owner_only_json, install_owner_only_json
        import hashlib
        root = store.root / 'containment' / hashlib.sha256(target.encode()).hexdigest()
        name = hashlib.sha256(args.request_id.encode()).hexdigest()
        accepted = root / (name + '-accepted.json'); terminal = root / (name + '-terminal.json')
        prior = _read_owner_only_json(accepted)
        if prior is not None:
            if prior != plan: raise SettlementError('request_conflict')
            receipt = _read_owner_only_json(terminal)
            if receipt is not None: return receipt
            raise SettlementError('persistence_uncertain')
        install_owner_only_json(accepted, plan)
        response = observer.containment(transaction=active, generation=args.expected_generation, containers=plan['containers'])
        if response.get('code') != 'contained': raise SettlementError('persistence_uncertain')
        receipt = {'schema_version': 1, 'ok': True, 'code': 'contained', 'request_id': args.request_id,
            'plan_digest': plan['plan_digest'], 'transaction_digest': plan['transaction_digest'],
            'generation': plan['generation']}
        install_owner_only_json(terminal, receipt)
        return receipt
