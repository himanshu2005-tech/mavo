"""Idempotent upgrade cleanup for a removed connector, before workers resume.

Keep only retirement identifiers here, never OAuth or execution implementations.
Do not delete Crew, routes, chat, or unrelated provider data.
"""

from sqlalchemy import select, update

from mavo.database import ActionRequest, CrewInstance, WorkspaceSetting, utcnow

RETIRED_OPERATIONS = {
    "outlook.send",
    "outlook.draft",
    "outlook.mark_read",
    "mscalendar.create",
    "mstasks.create",
    "mstasks.complete",
}


def retired_grant(value):
    return value.startswith("microsoft:") or value.removeprefix("action:") in RETIRED_OPERATIONS


def remove_retired_connector(factory, credentials):
    from mavo.crew_workers import stop_crew

    # Delete by exact credential identifier; no decryption or logging of tokens.
    credentials.delete("connector-microsoft-tokens")
    with factory() as session:
        for row in session.scalars(select(WorkspaceSetting)):
            if row.key == "connector-microsoft" or (
                row.key.startswith("action:") and row.key.rsplit(":", 1)[-1] in RETIRED_OPERATIONS
            ):
                session.delete(row)
        for crew in session.scalars(select(CrewInstance)):
            plan = dict(crew.configuration_plan or {})
            draft = dict(plan.get("draft") or {})
            affected = any(retired_grant(p) for p in crew.permissions + crew.resources)
            affected |= any(
                retired_grant(c) for c in draft.get("requested_connector_capabilities", [])
            )
            affected |= any(
                a.get("operation") in RETIRED_OPERATIONS for a in draft.get("requested_actions", [])
            )
            if not affected:
                continue
            stop_crew(session, crew)
            crew.permissions = [p for p in crew.permissions if not retired_grant(p)]
            crew.resources = [r for r in crew.resources if not retired_grant(r)]
            crew.runtime_state = "needs_input"
            crew.last_runtime_error = (
                "This Crew used a removed connector. "
                "Refine its setup to choose a supported resource."
            )
            if draft:
                draft["requested_connector_capabilities"] = [
                    c
                    for c in draft.get("requested_connector_capabilities", [])
                    if not retired_grant(c)
                ]
                draft["requested_actions"] = [
                    a
                    for a in draft.get("requested_actions", [])
                    if a.get("operation") not in RETIRED_OPERATIONS
                ]
                plan.update(
                    draft=draft,
                    phase="needs_input",
                    setup_confirmed=False,
                    user_paused=True,
                    questions=["Which supported resource should replace the removed connection?"],
                )
                crew.configuration_plan = plan
        session.execute(
            update(ActionRequest)
            .where(
                ActionRequest.operation.in_(RETIRED_OPERATIONS),
                ActionRequest.status.in_(["pending", "executing"]),
            )
            .values(
                status="rejected",
                error="Connector removed; this action cannot run.",
                finished_at=utcnow(),
            )
        )
        session.commit()
