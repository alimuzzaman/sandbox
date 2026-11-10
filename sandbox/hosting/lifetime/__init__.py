"""Lifetime and per-copy identity for hosted stacks and targets (spec 077).

``hoststore`` is the one authority for records: it is imported on the controller
and on the host, and shipped verbatim over SSH, so both sides decide the same way.
"""
