"""Feature-owned read-only delivery inspect command."""
from __future__ import annotations

import json
import shlex

from sandbox.delivery.service import inspect_with_factory
from sandbox.registry import CommandSpec, register_specs


_service_factory = None
_trace_factory = None


def configure(*, delivery_service_factory, trace_service_factory=None):
    global _service_factory, _trace_factory
    _service_factory = delivery_service_factory
    _trace_factory = trace_service_factory


def configure_parser(parser):
    parser.description = "Inspect retained delivery outcomes on this controller"
    parser.add_argument("action", choices=("inspect", "trace-capabilities", "trace-start", "trace-record",
                                           "trace-owner-status", "trace-owner-record"))
    parser.add_argument("--project-dir")
    parser.add_argument("--remote")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--environment")
    target.add_argument("--label")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--operation-id")
    selection.add_argument("--request-id")
    selection.add_argument("--trace-id")
    selection.add_argument("--trace-request-id")
    parser.add_argument("--mutation-id")
    parser.add_argument("--producer")
    parser.add_argument("--parent-request-id")
    parser.add_argument("--publication-id")
    parser.add_argument("--expected-sequence", type=int)
    parser.add_argument("--input-json")
    parser.add_argument("--observe", action="store_true", help="Add current read-only observations; does not refresh terminal history")
    parser.add_argument("--limit", type=int, choices=range(1, 51), metavar="1..50")
    parser.add_argument("--cursor")
    parser.add_argument("--json", action="store_true")


def format_projection(result):
    """Compact display of the same fields emitted through MCP/JSON."""
    lines = ["Query: serviced" if result["ok"] else "Query: failed"]
    if result.get("error") is not None:
        lines.append(result["error"]["code"] + ": " + result["error"]["message"])
    for name, title in (("latest_attempt", "Latest attempt"), ("latest_retained_complete_success", "Latest retained complete success")):
        operation = result.get(name)
        if operation is None:
            lines.append(title + ": unavailable")
        else:
            lines.append(title + ": " + operation["operation_id"] + " / " + operation["delivery_state"] + " / evidence " + operation["evidence_completeness"])
    selected = result.get("selected_operation")
    if selected is not None:
        lines.append("Selected attempt: " + selected["operation_id"] + " / " + selected["delivery_state"])
        application = selected["requested_outcome"]["application"]
        control = selected["requested_outcome"]["control"]
        lines.append("Application commit: " + (application["commit"] or "unavailable"))
        lines.append("Sandbox control commit: " + (control["source_commit"] or "unavailable"))
        lines.append("Failure stage: " + (selected["failure_stage"] or "none recorded"))
        if any(ref.get('kind') == 'original_delivery' for ref in selected['recovery_relations']):
            lines.append('Application and control metadata above describe the original deployment.')
        for effect in selected["effects"]:
            lines.append("Effect " + effect["name"] + ": " + effect["state"])
    for key, title in (("recorded_source_evidence", "Stored owner evidence"), ("current_observation", "Current observation")):
        if result.get(key):
            lines.append(title + ": " + ", ".join(name + "=" + block["state"] + "/" + block["result"] for name, block in result[key].items() if block is not None))
    history = result.get("history")
    if history is not None:
        lines.append("History: " + history["completeness"] + "; " + str(history["returned_count"]) + " returned")
        if history["next_cursor"] is not None:
            lines.append("Next cursor: " + history["next_cursor"])
    action = result.get("next_action")
    lines.append("Next action: " + (action["command"] if action else "none"))
    if action:
        selectors = action["selectors"]
        if action["command"] == "job-status":
            argv = ["./sb", "job-status", selectors["job_id"], "--json"]
        elif action["command"] == "host image status":
            argv = ["./sb", "host", "image", "status", "--project-dir", "."]
            for key in ("remote", "environment", "request_id"):
                argv.extend(["--" + key.replace("_", "-"), selectors[key]])
            lines.append("Run from the inspected project directory using the same controller and Sandbox home.")
        else:
            argv = None
        if argv is not None:
            lines.append("If ./sb is elsewhere, use the absolute path to the same Sandbox executable.")
            lines.append("Command template: " + shlex.join(argv))
    if result.get("next_action_reason") is not None:
        lines.append(result["next_action_reason"]["message"])
    recoveries = result.get('recovery_operations')
    if recoveries is not None:
        lines.append('Related recovery evidence: ' + recoveries['completeness'])
        for item in recoveries['operations']:
            lines.append('Recovery: ' + item['operation_id'] + ' / ' + item['delivery_state'])
        if recoveries['omitted']:
            lines.append('Omitted recovery records: ' + str(recoveries['omitted']))
    return "\n".join(lines)


def cmd_delivery(cfg, args):
    trace_mode = args.action != 'inspect' or getattr(args, 'trace_id', None) is not None or getattr(args, 'trace_request_id', None) is not None
    if trace_mode:
        from sandbox.delivery.trace_service import trace_with_factory, format_trace_projection
        result = trace_with_factory(_trace_factory, args.action, **{
            key: getattr(args, key, None) for key in (
                'project_dir', 'remote', 'environment', 'label', 'operation_id', 'request_id',
                'trace_id', 'trace_request_id', 'mutation_id', 'producer', 'parent_request_id',
                'publication_id', 'expected_sequence', 'input_json', 'observe', 'limit', 'cursor')})
        print(json.dumps(result, sort_keys=True, separators=(',', ':')) if getattr(args, 'json', False)
              else format_trace_projection(result))
        if not result['ok']:
            raise SystemExit(1)
        return
    if any(getattr(args, key, None) is not None for key in
           ('mutation_id', 'producer', 'parent_request_id', 'publication_id', 'expected_sequence', 'input_json')):
        result = {'schema_version': 1, 'ok': False, 'error': {
            'code': 'delivery_contract_invalid', 'message': 'Invalid delivery selectors.'}}
        print(json.dumps(result) if getattr(args, 'json', False) else format_projection(result))
        raise SystemExit(1)
    result = inspect_with_factory(_service_factory, **{
        name: (getattr(args, name, default) if getattr(args, name, default) is not None else default) for name, default in (
            ("project_dir", None), ("remote", None), ("environment", None), ("label", None),
            ("operation_id", None), ("request_id", None), ("observe", False), ("limit", 10), ("cursor", None),
        )
    })
    print(json.dumps(result, sort_keys=True, separators=(",", ":")) if getattr(args, "json", False) else format_projection(result))
    if not result["ok"]:
        raise SystemExit(1)


register_specs((CommandSpec(
    name="delivery", handler=cmd_delivery, configure=configure_parser,
    owner=__name__, order=207, scope="global", destructive=False,
    predispatch_policy=lambda _args: True,
    help="Inspect recorded delivery outcomes without changing owner state",
),))
