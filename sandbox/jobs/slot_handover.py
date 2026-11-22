"""Hand a releasing job's host slot to the nested job running on it.

A nested job may run on its parent's slot (``scheduler.borrows_parent_slot``).
The parent can end first (a stack ``up`` submits its apply and returns), so
every path that drops a job's slot calls :func:`release_slot`, which moves the
slot to an active borrowing child instead of deleting it. The child then holds
the slot like any admitted job, and its own children can keep borrowing it.
"""
from __future__ import annotations

ACTIVE = ("accepted", "queued", "running", "cancelling")


def borrowing_child(connection, job_id: str) -> str | None:
    """The nearest active descendant of ``job_id`` admitted without a slot of its
    own (a child, or a grandchild whose own parent already ended)."""
    row = connection.execute(
        "WITH RECURSIVE tree(job_id, depth) AS ("
        " SELECT job_id, 1 FROM jobs WHERE parent_job_id=?"
        " UNION ALL SELECT j.job_id, t.depth + 1 FROM jobs j JOIN tree t"
        " ON j.parent_job_id=t.job_id WHERE t.depth < 128) "
        "SELECT c.job_id FROM tree t JOIN jobs c ON c.job_id=t.job_id "
        "JOIN workspace_leases l ON l.job_id=c.job_id "
        "WHERE c.lifecycle IN (?,?,?,?) "
        "AND NOT EXISTS (SELECT 1 FROM host_capacity_leases h WHERE h.job_id=c.job_id) "
        "ORDER BY t.depth, c.accepted_at, c.job_id LIMIT 1", (job_id, *ACTIVE)).fetchone()
    return None if row is None else str(row[0])


def release_slot(connection, job_id: str) -> None:
    """Drop ``job_id``'s slot, or give it to the child that was running on it."""
    child = borrowing_child(connection, job_id)
    held = connection.execute(
        "SELECT 1 FROM host_capacity_leases WHERE job_id=?", (job_id,)).fetchone()
    if child is not None and held is not None:
        connection.execute("UPDATE host_capacity_leases SET job_id=? WHERE job_id=?",
                           (child, job_id))
        return
    if child is not None:
        # ``job_id`` itself ran on a borrowed slot; the ancestor holding that slot
        # still covers the descendant and hands it over when it ends.
        return
    connection.execute("DELETE FROM host_capacity_leases WHERE job_id=?", (job_id,))
