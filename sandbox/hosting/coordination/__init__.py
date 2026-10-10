"""Per-target hosting coordination (spec 060).

The remote coordination store under ``<remote SANDBOX_HOME>/runtime/hosting-leases``
is owned by ``program`` (the fixed remote program) and reached only through
``client``. Nothing else names that directory.
"""
