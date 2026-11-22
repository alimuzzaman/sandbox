"""Nested jobs on a waiting parent's host slot, and handing that slot over.

One host slot covers one chain: the job holding it, and below it at most one
line of nested jobs admitted without a slot (each the child of the one above,
ended ones allowed in between). ``may_borrow`` lets a new nested job join only
at the end of that line, so a slot never covers two running branches.

The holder can end first (a stack ``up`` submits its apply and returns), so
every path that drops a job's slot calls :func:`release_slot`, which moves the
slot to the nearest active job of its line instead of deleting it. A nested job
that ends without a slot changes nothing: the holder still covers the rest.
"""
from __future__ import annotations

from .server_cli_lineage import MAX_ANCESTORS

ACTIVE = ("accepted", "queued", "running", "cancelling")


def _holds_slot(connection, job_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM host_capacity_leases WHERE job_id=?", (job_id,)).fetchone() is not None


def _line(connection, holder: str) -> list[str]:
    """Active jobs without a slot under ``holder``, nearest first. The walk stops
    at descendants holding a slot of their own: their subtree is theirs."""
    rows = connection.execute(
        "WITH RECURSIVE tree(job_id, depth) AS ("
        " SELECT job_id, 1 FROM jobs WHERE parent_job_id=?"
        " AND NOT EXISTS (SELECT 1 FROM host_capacity_leases h WHERE h.job_id=jobs.job_id)"
        " UNION ALL SELECT j.job_id, t.depth + 1 FROM jobs j JOIN tree t"
        " ON j.parent_job_id=t.job_id WHERE t.depth < ?"
        " AND NOT EXISTS (SELECT 1 FROM host_capacity_leases h WHERE h.job_id=j.job_id)) "
        "SELECT c.job_id FROM tree t JOIN jobs c ON c.job_id=t.job_id "
        "JOIN workspace_leases l ON l.job_id=c.job_id WHERE c.lifecycle IN (?,?,?,?) "
        "ORDER BY t.depth, c.accepted_at, c.job_id",
        (holder, MAX_ANCESTORS, *ACTIVE)).fetchall()
    return [str(row[0]) for row in rows]


def _holder_path(connection, job_id: str) -> tuple[str | None, list[str]]:
    """The slot holder covering ``job_id`` and the jobs from ``job_id`` up to it."""
    path = []
    current = job_id
    for _ in range(MAX_ANCESTORS):
        if _holds_slot(connection, current):
            return current, path
        path.append(current)
        row = connection.execute(
            "SELECT parent_job_id FROM jobs WHERE job_id=?", (current,)).fetchone()
        if row is None or row[0] is None:
            return None, path
        current = str(row[0])
    return None, path


def may_borrow(connection, row) -> bool:
    """A nested job may run on the slot covering its running, admitted parent
    when every active job already on that slot's line is its own ancestor."""
    row = dict(row)
    parent_id = row.get("parent_job_id")
    if not parent_id:
        return False
    parent = connection.execute(
        "SELECT lifecycle FROM jobs WHERE job_id=?", (parent_id,)).fetchone()
    # A cancelling parent is about to stop waiting; its child takes a slot.
    if parent is None or parent[0] != "running":
        return False
    if connection.execute("SELECT 1 FROM workspace_leases WHERE job_id=?",
                          (parent_id,)).fetchone() is None:
        return False
    holder, path = _holder_path(connection, str(parent_id))
    if holder is None:
        return False
    ancestors = set(path)
    return all(job in ancestors for job in _line(connection, holder) if job != row["job_id"])


def release_slot(connection, job_id: str) -> None:
    """Drop ``job_id``'s slot, or give it to the nearest active job of its line."""
    if not _holds_slot(connection, job_id):
        return
    line = _line(connection, job_id)
    if line:
        connection.execute("UPDATE host_capacity_leases SET job_id=? WHERE job_id=?",
                           (line[0], job_id))
        return
    connection.execute("DELETE FROM host_capacity_leases WHERE job_id=?", (job_id,))
