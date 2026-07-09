from __future__ import annotations
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Shared fan-out helper (docs/ci-e2e-runner-spec.md §4.2): boot N labelled
# instances of one project root CONCURRENTLY under a cap, run a worker
# function against each, collect results, tear down the ephemeral instances
# afterward. This is the ONE concurrency mechanism for both the e2e
# multi-worker runner and the CI matrix runner — neither duplicates pool
# logic. Threads (not processes) because the work here is I/O-bound
# (subprocess/docker calls release the GIL); no CPU-bound work happens here.

_TERMINAL_OK = {"passed", "ok", "skipped", "noop"}


def _default_concurrency(cfg: dict, requested: int | None) -> int:
    """Resolution order: explicit arg > $SANDBOX_MAX_CONCURRENCY >
    sandbox.local.yml `concurrency:` > a laptop-safe default derived from CPU
    count. Never below 1. The default (capped at 4) is deliberately
    conservative — each unit is a full WordPress+MySQL+Mailpit docker stack;
    see docs/ci-e2e-runner-spec.md §7 (resource exhaustion is the headline risk)."""
    if requested:
        return max(1, int(requested))
    env = os.environ.get("SANDBOX_MAX_CONCURRENCY")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    cfg_val = (cfg or {}).get("concurrency")
    if cfg_val:
        try:
            return max(1, int(cfg_val))
        except (TypeError, ValueError):
            pass
    cpu = os.cpu_count() or 2
    return max(2, min(4, cpu // 2 or 1))


def _teardown_instance(instance_name: str) -> None:
    """Best-effort delete of an ephemeral fan-out instance by name. Failures
    are swallowed (not raised) — a teardown hiccup must not mask the actual
    test/CI result the caller is waiting on; a leftover stack is visible via
    `./sb instances` for manual cleanup."""
    try:
        subprocess.run([str(ROOT / "sb"), "instance", "delete", instance_name, "--yes"],
                       capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    except Exception:
        pass


def run_across_instances(cfg: dict, root: str, specs: list[dict], worker_fn,
                         *, concurrency: int | None = None,
                         keep_on_fail: bool = False,
                         strict_provision: bool = False,
                         on_progress=None) -> dict:
    """Boot one labelled instance per entry in `specs` (concurrently, capped),
    run `worker_fn(instance_entry, spec)` against each, collect results, and
    tear down every ephemeral instance afterward — UNLESS `keep_on_fail` is
    set and that particular unit failed, in which case its stack is left
    running for inspection (`wp_cli`/`visit` with that `label`).

    specs: `[{"label": str, "php": str | None, "wp": str | None}, ...]`. Each
    label is minted via `ensure_instance(cfg, root, label=label, create=True)`
    — callers own the label-prefix convention (`e2e-w*`, `ci-<runid>-*`); this
    helper does not invent one.

    worker_fn(entry, spec) -> dict: MUST return a dict with at least a
    `status` key (`"passed"`/`"failed"`/`"skipped"`/`"noop"`/anything else is
    treated as failed for the purpose of the aggregate `ok`). Raising from
    worker_fn is caught and recorded as `status: "failed"` for that unit only
    — one unit's exception never aborts the others.

    Provisioning failure (the instance itself won't boot) is recorded as
    `status: "provision_failed"` and, by default, does NOT abort the run —
    the remaining units still get a chance (`strict_provision=True` flips
    this to fail-fast by re-raising instead).

    Returns `{"ok": bool, "concurrency": int, "units": [ {label, spec,
    provision, result, status, error?} ]}`, `units` in the same order as
    `specs`.
    """
    cap = _default_concurrency(cfg, concurrency)
    results: dict[str, dict] = {}
    lock = threading.Lock()

    def _progress(event: dict) -> None:
        if on_progress:
            try:
                on_progress(event)
            except Exception:
                pass

    def _run_one(spec: dict) -> dict:
        label = spec["label"]
        unit = {"label": label, "spec": spec, "provision": None,
                "result": None, "status": "pending"}
        try:
            _progress({"phase": "provision", "label": label})
            # A spec MAY carry php/wp overrides (the CI runner's matrix cells
            # do — docs/ci-e2e-runner-spec.md "CI takes priority over
            # sandbox.config"); e2e specs simply omit them and get the
            # project's own configured version, unchanged. A spec MAY also
            # carry a `config_label` — a run-independent key for an optional
            # sandbox.config.<config_label>.json layer, distinct from the
            # (possibly randomized, per-run) instance `label` itself.
            entry = ensure_instance(cfg, root, label=label, create=True,
                                    php_version=spec.get("php"),
                                    wp_version=spec.get("wp"),
                                    config_label=spec.get("config_label"))
            unit["provision"] = entry
        except Exception as e:
            unit["status"] = "provision_failed"
            unit["error"] = str(e)
            _progress({"phase": "provision_failed", "label": label, "error": str(e)})
            if strict_provision:
                raise
            return unit

        try:
            _progress({"phase": "run", "label": label})
            result = worker_fn(entry, spec)
            unit["result"] = result
            unit["status"] = (result or {}).get("status", "passed")
        except Exception as e:
            unit["status"] = "failed"
            unit["error"] = str(e)
        finally:
            failed = unit["status"] not in _TERMINAL_OK
            _progress({"phase": "done", "label": label, "status": unit["status"]})
            # Delete by the instance's real NAME (unique, docker-compose-project-
            # bearing) — not by `label`, which is only unique within this root
            # and isn't what `./sb instance delete` operates on.
            inst_name = (unit.get("provision") or {}).get("instance")
            if inst_name and not (keep_on_fail and failed):
                _teardown_instance(inst_name)
        return unit

    with ThreadPoolExecutor(max_workers=max(1, cap)) as ex:
        futures = {ex.submit(_run_one, spec): spec["label"] for spec in specs}
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                unit = fut.result()
            except Exception as e:
                unit = {"label": label, "spec": None, "provision": None,
                        "result": None, "status": "provision_failed", "error": str(e)}
            with lock:
                results[label] = unit

    ordered = [results[s["label"]] for s in specs if s["label"] in results]
    ok_all = all(u["status"] in _TERMINAL_OK for u in ordered)
    return {"ok": ok_all, "concurrency": cap, "units": ordered}
