from __future__ import annotations
import base64
import json

from app import _project_instance, _require_project_capability, _wpcli, mcp


@mcp.tool()
def wp_eval_live(code: str, *, project_dir: str, label: str | None = None) -> dict:
    """Run PHP in the live WordPress runtime via the in-instance `sandbox/execute-php`
    ability (spec 003), returning its structured result: success, return_value,
    captured output, warnings/notices, error_message/class on failure, timing.

    This is the host-side proxy for the in-instance Abilities layer — the same
    capability external MCP clients reach directly at /wp-json/sandbox/mcp. Requires
    the abilities layer enabled on the instance (./sb abilities on).

    project_dir: the plugin project to target (call ensure_instance first).
    """
    capability_error = _require_project_capability(project_dir, label, "wordpress.abilities")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    runner = (
        "if(!function_exists('sandbox_abilities_execute_php')){"
        "echo '{\"ok\":false,\"error\":\"abilities layer not active — run ./sb abilities on\"}';return;}"
        "wp_set_current_user(1);"
        f"echo wp_json_encode(sandbox_abilities_execute_php(['code'=>base64_decode('{b64}')]));"
    )
    r = _wpcli(["eval", runner], instance=inst, timeout=60)
    out = (r.get("stdout") if isinstance(r, dict) else None) or ""
    start = out.find("{")
    if start < 0:
        return {"ok": False, "error": "no JSON from execute-php", "raw": out[:500]}
    try:
        parsed = json.loads(out[start:])
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"bad JSON ({e})", "raw": out[:500]}
    return {"ok": True, "result": parsed}
