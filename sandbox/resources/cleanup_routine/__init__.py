"""Scheduled safe cleanup on a remote host (spec 057).

The routine is enabled, disabled and inspected through the authenticated
``/resources`` control route and runs on the host as a ``systemd --user``
timer.  Each run is one safe-tier reclamation pass through the existing
:class:`~sandbox.resources.reclaim_service.ReclaimService`.

Submodules: ``contract`` (request/response validation), ``units`` (timer
render/install/remove), ``store`` (config and run records), ``run`` (the
timer's entry point) and ``host`` (the control-route handler).
"""

__all__: list[str] = []
