"""Controller-owned planning and strict remote lifecycle orchestration."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import HostMemoryStatusProjection, canonical_digest
from .models import bounded
from .policy import PolicyRefusal, build_plan, freshness, sustained_swap_use
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
    def __init__(self, remote, repository, *, now=None):
        self.remote=remote; self.repository=repository; self.now=now or (lambda:datetime.now(timezone.utc))
    @property
    def target(self): return {"kind":"remote","name":self.remote.name}

    def status(self,budget_seconds=15):
        try: data=self.remote.call("host_memory_status",budget_seconds=budget_seconds)
        except Exception as exc: return failure("swap-status",exc,self.target,"failed")
        data = dict(data)
        monitor = dict(data.get("monitor") or {})
        latest = monitor.get("latest_sample_at")
        if latest:
            try: monitor["freshness"] = freshness(latest, self.now())
            except (TypeError, ValueError): monitor["freshness"] = "malformed"
        recent = data.pop("recent_samples", None)
        if isinstance(recent, list):
            monitor["sustained_swap_use"] = sustained_swap_use(recent)
        data["monitor"] = monitor
        try: data = bounded(data, 256 * 1024)
        except (TypeError, ValueError) as exc:
            return failure("swap-status", exc, self.target, "failed")
        return envelope("swap-status","complete" if data.get("evidence_state")=="known" else "partial",target=self.target,data=data,error=None if data.get("evidence_state")=="known" else {"code":"evidence_partial","message":"host evidence is incomplete","retryable":True})

    def projection(self,status):
        mem=status.get("memory") or {}; areas=status.get("swap_areas") or []; monitor=status.get("monitor") or {}
        return HostMemoryStatusProjection(target_identity=str(status.get("target_identity","unknown")),observed_at=str(status.get("observed_at","")),evidence_state=str(status.get("evidence_state","unknown")),memory_total_bytes=mem.get("total_bytes"),memory_available_bytes=mem.get("available_bytes"),swap_total_bytes=sum(a.get("total_bytes",0) for a in areas),swap_used_bytes=sum(a.get("used_bytes",0) for a in areas),ownership=str(status.get("ownership","unknown")),monitor_freshness=str(monitor.get("freshness","unknown")),sustained_swap_use=monitor.get("sustained_swap_use"),pressure_state=str(monitor.get("pressure_state","unknown")),operation_block=(status.get("operation_block") or {}).get("reason"))

    def plan(self,operation,*,size_gib=4,budget_seconds=15):
        status=self.status(budget_seconds)
        if not status.get("ok"): return failure("swap-plan",PolicyRefusal((status.get("error") or {}).get("code","response_invalid"),"status evidence is not authorizing"),self.target)
        try:
            state=status["data"]; target={"remote_name":self.remote.name,"target_identity":state.get("target_identity",self.remote.record.get("identity","unknown")),"service_ownership_marker":self.remote.marker,"runtime_revision":self.remote.revision}
            plan=build_plan(operation,target,state,size_gib=size_gib,now=self.now()); self.repository.save_plan(plan)
            return envelope("swap-plan","planned",target=self.target,data=plan)
        except Exception as exc: return failure("swap-plan",exc,self.target)

    def apply(self,plan_id,*,confirm=False,budget_seconds=300):
        if not confirm: return failure("swap-apply",PolicyRefusal("confirmation_required","exact plan confirmation is required"),self.target)
        try: plan=self.repository.load_plan(plan_id)
        except Exception as exc: return failure("swap-apply",PolicyRefusal("plan_not_found","plan was not found"),self.target)
        operation_id=canonical_digest({"plan_id":plan_id,"target":plan["target"]})
        canonical={"plan_id":plan_id,"operation":plan["operation"],"target_identity":plan["target"]["target_identity"],"service_ownership_marker":plan["target"]["service_ownership_marker"],"runtime_revision":plan["target"]["runtime_revision"],"expires_at":plan["expires_at"],"observation_digest":plan["observation_digest"],"effective_policy":plan["effective_policy"],"intended_artifact_digests":{name:canonical_digest({"logical_id":name}) for name in plan["intended_changes"]},"rollback_scope":plan["rollback_scope"]}
        try: result=self.remote.call("host_memory_apply",operation_id=operation_id,plan=canonical,confirmed=True,budget_seconds=budget_seconds)
        except Exception as exc: return failure("swap-apply",exc,self.target,"partial")
        status=result.get("status","partial")
        if status not in {"applied","already_current","refused","partial","failed","rollback_complete","rollback_incomplete"}: return failure("swap-apply",PolicyRefusal("response_invalid","remote returned a non-normative outcome"),self.target,"partial")
        return envelope("swap-apply",status,target=self.target,data=result.get("data") or {},error=result.get("error"))

    def history(self,*,since=None,until=None,limit=288,budget_seconds=15):
        try: data=self.remote.call("host_memory_history",since=since,until=until,limit=limit,budget_seconds=budget_seconds)
        except Exception as exc: return failure("swap-history",exc,self.target,"failed")
        return envelope("swap-history","partial" if data.get("truncated") or not data.get("complete",True) else "complete",target=self.target,data=data,error=None)
