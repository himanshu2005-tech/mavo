"""Crew-owned updates for verified Google changes, never setup previews or model claims."""

from collections import Counter

from sqlalchemy import select

from mavo.connectors.catalog import action_grant
from mavo.crew_composition import effective_permissions
from mavo.database import ActionRequest, GoogleAccount

OPERATIONS = {
    "calendar.create",
    "calendar.update",
    "calendar.delete",
    "tasks.create",
    "tasks.update",
    "tasks.complete",
    "tasks.delete",
}


def completed_changes(session, run, owner):
    if not owner or not run or run.status != "done":
        return {}
    account = session.get(GoogleAccount, "google")
    if not account:
        return {}
    counts = Counter()
    for agent in owner.agents:
        worker = agent.worker
        if (
            agent.status != "active"
            or not worker
            or agent.execution.get("task_id") != run.id
            or worker.configuration_plan.get("first_run_source")
        ):
            continue
        allowed = effective_permissions(session, agent)
        for action in session.scalars(
            select(ActionRequest).where(
                ActionRequest.crew_id == worker.id,
                ActionRequest.generation == worker.runtime_generation,
                ActionRequest.source == "crew",
                ActionRequest.account_subject == account.subject,
                ActionRequest.status == "completed",
                ActionRequest.created_at >= run.created_at,
                ActionRequest.operation.in_(OPERATIONS),
            )
        ):
            if action_grant(action.operation) in allowed:
                counts[action.operation] += 1
    return dict(counts)
