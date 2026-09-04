"""Controller-owned planning and strict remote lifecycle orchestration."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import HostMemoryStatusProjection, RemoteSwapState
from .repository import RepositoryError


def envelope(action,status,*,target=None,data=None,error=None):
    return {"schema_version":1,"ok":error is None and status not in {"refused","partial","failed","rollback_incomplete"},"action":action,"status":status,"target":target,"data":data or {},"error":error}


def failure(action,exc,target=None,status="refused"):
    code=getattr(exc,"code",None)
    if not code:
        candidate=str(exc)
        code=candidate if candidate.replace("_","").isalnum() else "response_invalid"
    return envelope(action,status,target=target,error={"code":str(code)[:64],"message":str(exc).replace("\n"," ")[:240],"retryable":code in {"remote_unreachable","response_invalid"}})


class HostMemoryService:
    """Internal status adapter. Public consumers receive only value objects."""
    def __init__(self, remote, *, now=None):
        self._remote=remote; self._now=now or (lambda:datetime.now(timezone.utc))
    @property
    def target(self): return {"kind":"remote","name":self._remote.name}

    def status(self,budget_seconds=15):
        try: data=self._remote.call("host_memory_status",budget_seconds=budget_seconds)
        except Exception as exc: return failure("swap-status",exc,self.target,"failed")
        try:
            data = RemoteSwapState.from_dict(data, require_digest=True).to_dict()
        except (TypeError, ValueError):
            return failure("swap-status", RepositoryError("response_invalid"),
                           self.target,"failed")
        return envelope("swap-status","complete" if data.get("evidence_state")=="known" else "partial",target=self.target,data=data,error=None if data.get("evidence_state")=="known" else {"code":"evidence_partial","message":"host evidence is incomplete","retryable":True})

    def projection(self,status):
        mem=status.get("memory") or {}; areas=status.get("swap_areas") or []; monitor=status.get("monitor") or {}
        return HostMemoryStatusProjection(target_identity=str(status.get("target_identity","unknown")),observed_at=str(status.get("observed_at","")),evidence_state=str(status.get("evidence_state","unknown")),memory_total_bytes=mem.get("total_bytes"),memory_available_bytes=mem.get("available_bytes"),swap_total_bytes=sum(a.get("total_bytes",0) for a in areas),swap_used_bytes=sum(a.get("used_bytes",0) for a in areas),ownership=str(status.get("ownership","unknown")),monitor_freshness=str(monitor.get("freshness","unknown")),sustained_swap_use=monitor.get("sustained_swap_use"),pressure_state=str(monitor.get("pressure_state","unknown")),operation_block=(status.get("operation_block") or {}).get("reason"))

    def plan(self,size_gib=4,budget_seconds=15):
        """Build one deterministic controller-owned enable plan from status evidence.

        Read-only: the only remote action is ``host_memory_status``. There is no
        remote plan action and no provider mutation on this path.
        """
        from .policy import PolicyRefusal, build_plan
        observed=self.status(budget_seconds=budget_seconds)
        state=observed.get("data") or {}
        target={"remote_name":self._remote.name,
                "target_identity":str(state.get("target_identity","unknown")),
                "service_ownership_marker":str(getattr(self._remote,"marker","")),
                "runtime_revision":str(getattr(self._remote,"revision",""))}
        try:
            plan=build_plan("enable",target,state,size_gib=size_gib,now=self._now())
        except PolicyRefusal as exc:
            return failure("swap-plan",exc,self.target)
        return envelope("swap-plan",plan["state"],target=self.target,data=plan)
