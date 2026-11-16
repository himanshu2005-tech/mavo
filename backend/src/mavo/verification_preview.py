"""Server-bound, non-executing action previews during private first-run tests."""

import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from mavo.connectors.catalog import action_grant, provider_for
from mavo.database import CrewInstance, GoogleAccount, ModelRoute, WorkspaceSetting, utcnow
from mavo.google_actions import MODELS

PREVIEW_PROTOCOL = 2
READ_ACTIONS = {
    "browser.open",
    "browser.read",
    "browser.search",
    "browser.navigate",
    "browser.scroll",
    "browser.wait",
    "browser.close",
    "windows.list",
    "notes.find",
    "notes.read",
    "files.list",
    "files.read",
    "files.find",
    "project.read",
}
# Every registered operation is covered. New effectful tools default to preview,
# never execution; adding a tool cannot accidentally opt it into the read allowlist.
PREVIEW_ACTIONS = set(MODELS) - READ_ACTIONS
WRITE_GRANTS = {action_grant(operation) for operation in PREVIEW_ACTIONS}
REVIEW_REQUIRED = "Review the test preview before enabling changes"


def action_preview_record(session, runtime, source, workers=()):
    """Persist honest coverage, including conditional operations with no sample input."""
    from mavo.database import ActionRequest

    declared = {
        a["operation"]
        for a in source.configuration_plan.get("draft", {}).get("requested_actions", [])
    }
    declared |= {
        op
        for a in source.agents
        if a.status == "active"
        for op in (a.definition or {}).get("actions", [])
    }
    candidates = declared & PREVIEW_ACTIONS
    candidates |= {
        op
        for op in PREVIEW_ACTIONS
        if action_grant(op) in source.permissions
        and runtime.actions.rules.read(session, op, source.id)["enabled"]
    }
    rows = (
        session.scalars(
            select(ActionRequest).where(
                ActionRequest.crew_id.in_(list(workers)), ActionRequest.status == "previewed"
            )
        ).all()
        if workers
        else []
    )
    proposed = [
        {
            "operation": a.operation,
            "arguments": a.arguments,
            "checks": a.result.get("checks", []),
            "not_verified": a.result.get("not_verified", []),
        }
        for a in rows
    ]
    if "device:clean_temp" in source.permissions:
        from mavo.resources.device_config import monitor_for

        monitor = monitor_for(source)
        if monitor.clean_temp_days:
            proposed.append(
                {
                    "operation": "device.clean_temp",
                    "arguments": {
                        "older_than_days": monitor.clean_temp_days,
                        "empty_recycle_bin": monitor.empty_recycle_bin,
                    },
                    "checks": ["configuration", "saved_access"],
                    "not_verified": ["execution_result", "freed_space", "runtime_preconditions"],
                }
            )
    if not candidates and not proposed and not WRITE_GRANTS.intersection(source.permissions):
        return {}
    if len(proposed) > 128 or len(json.dumps(proposed)) > 256000:
        raise ValueError(
            "The setup preview is too large to review safely. Reduce the work in one "
            "cycle and test again; nothing was executed."
        )
    from mavo.action_rules import CATALOG

    for action in proposed:
        action["label"] = CATALOG.get(action["operation"], ("Device", "Clean temporary files"))[1]
    checked = {a["operation"] for a in proposed}
    return {
        "kind": "action_preview",
        "confirmed": False,
        "proposed_actions": proposed,
        "unexercised_actions": sorted(candidates - checked),
        "previewed_action_count": len(proposed),
        "message": "Read-only setup checks completed. Proposed actions were validated "
        "without execution. Conditional actions without a sample input were not exercised. "
        "Review before enabling; nothing was sent, changed, deleted or run.",
    }


def private_verification_owner(session, crew_id):
    from mavo.crew_composition import execution_agent

    worker = session.get(CrewInstance, crew_id) if crew_id else None
    if not worker:
        return None
    member = execution_agent(session, crew_id)
    parent = session.get(CrewInstance, member.crew_id) if member else worker
    if not parent or not parent.configuration_plan.get("first_run_source"):
        return None
    return parent


def verification_parent(session, crew_id):
    from mavo.crew_composition import validate_worker
    from mavo.first_run import is_verification

    parent = private_verification_owner(session, crew_id)
    if not parent:
        return None
    worker = session.get(CrewInstance, crew_id)
    member = worker.id != parent.id
    if member:
        validate_worker(session, worker)
    if not is_verification(session, parent):
        raise ValueError("First-run verification changed or ended; this action cannot execute")
    return parent


def validate_local_targets(engine, session, row):
    """Read-only path checks; never call helpers that create a default workspace."""
    args = row.arguments
    rule = engine.rule_for(session, row.operation, row.crew_id)
    if row.operation == "terminal.run":
        if not Path(args["folder"]).resolve(strict=True).is_dir():
            raise ValueError("Choose an existing terminal folder")
    if row.operation in {"files.move", "files.backup", "notes.write"}:
        from mavo.local_files import inside, roots

        if row.operation == "notes.write":
            target = inside(args["path"], rule, must_exist=False)
            if target.exists() and (not target.is_file() or args.get("mode", "create") == "create"):
                raise ValueError("The note target already exists; choose append or another file")
        else:
            source = inside(args.get("source") or args["folder"], rule)
            destination = inside(args["destination_folder"], rule, must_exist=False)
            if source in roots(rule) and row.operation == "files.move":
                raise ValueError("An allowed folder itself cannot be moved")
            if destination.exists() and not destination.is_dir():
                raise ValueError("The destination must be a folder")
            if source.is_dir() and (destination == source or destination.is_relative_to(source)):
                raise ValueError("Choose a destination outside the source folder")
            if (
                row.operation == "files.move"
                and (destination / (args.get("new_name") or source.name)).exists()
            ):
                raise ValueError("The destination file already exists; nothing was moved")
    if row.operation.startswith("project."):
        from mavo.projects import Projects, is_link, slug_of

        configured = getattr(getattr(engine.desktop, "projects", None), "default_root", None)
        root = Path(
            (rule.get("folders") or [configured or Path.home() / "Documents" / "Mavo Projects"])[0]
        ).resolve()
        folder = root / slug_of(args.get("project") or args["name"])
        if is_link(folder) or not folder.resolve().is_relative_to(root):
            raise ValueError("The project must remain inside its configured folder")
        for path in (args.get("path"), args.get("script"), args.get("file")):
            if path:
                Projects().locate(folder, path)


def preview_action(engine, session, row):
    from mavo.google_actions import ActionPolicyError, view

    try:
        parent = verification_parent(session, row.crew_id)
    except ValueError as exc:
        raise ActionPolicyError(str(exc)) from None
    if not parent:
        return None
    source_id = parent.configuration_plan["first_run_source"]
    if engine.rules.read(session, row.operation, source_id) != engine.rules.read(
        session, row.operation, row.crew_id
    ):
        raise ActionPolicyError("Action access changed during verification. Test again.")
    if row.operation in READ_ACTIONS:
        return None
    if row.operation not in PREVIEW_ACTIONS:
        raise ActionPolicyError(
            "This operation cannot run during a setup preview. Nothing changed."
        )
    # Revalidate typed arguments, actor, account consent, resource ceiling and saved policy.
    from pydantic import ValidationError

    try:
        MODELS[row.operation].model_validate(row.arguments)
    except ValidationError as exc:
        fields = [".".join(str(p) for p in e["loc"]) for e in exc.errors(include_input=False)[:3]]
        raise ActionPolicyError("Invalid preview arguments: " + ", ".join(fields)) from None
    engine.check_actor(session, row)
    try:
        validate_local_targets(engine, session, row)
    except (ValueError, OSError) as exc:
        raise ActionPolicyError(str(exc)) from None
    provider = provider_for(row.operation)
    if provider != "local":
        if getattr(engine, "connectors", None):
            connected = engine.connectors.capability_available(row.operation)
        else:
            from mavo.google_actions import required_capability

            connected = required_capability(row.operation) in engine.auth.status().get(
                "capabilities", []
            )
        if not connected:
            raise ActionPolicyError("Connect the account and grant access for " + row.operation)
    existing = row.operation in {
        "gmail.archive",
        "gmail.mark_read",
        "gmail.trash",
        "calendar.update",
        "calendar.delete",
        "tasks.update",
        "tasks.complete",
        "tasks.delete",
    }
    if existing:
        worker = session.get(CrewInstance, row.crew_id)
        service = row.operation.split(".")[0]
        if "google:" + service + ":read" not in worker.permissions:
            raise ActionPolicyError("Read access is required to preview an existing item")
        account = session.get(GoogleAccount, "google")
        items = account.cursors.get(service, {}).get("snapshot", []) if account else []
        target_id = row.arguments.get("item_id") or row.arguments.get("message_id")
        target = next((i for i in items if i.get("id") == target_id), None)
        if not target or (
            row.arguments.get("etag") and target.get("etag") != row.arguments["etag"]
        ):
            raise ActionPolicyError(
                "Read the existing item and refresh its ID/version before previewing a change"
            )
    row.status = "previewed"
    row.finished_at = utcnow()
    row.result = {
        "verification": "validated_proposal_only",
        "applied": False,
        "operation": row.operation,
        "arguments": row.arguments,
        "checks": [
            "arguments",
            "current_access",
            "saved_scope",
            *(["connected_account"] if provider != "local" else []),
            *(["observed_target"] if existing else []),
        ],
        "not_verified": [
            "execution_result",
            "runtime_preconditions",
            *(["script_approval"] if row.operation == "project.run" else []),
        ],
        "message": "Preview only. Nothing was executed, sent, changed or deleted; "
        "no real output or new item ID was issued. Finish with the proposed plan, "
        "not a claim of execution.",
    }
    session.commit()
    return view(row)


def activation_binding(session, runtime, crew):
    """Bind confirmation to the exact configuration, actor, consent and saved rules tested."""
    draft = crew.configuration_plan.get("draft", {})
    route = session.get(ModelRoute, crew.route_id) if crew.route_id else None
    account = session.get(GoogleAccount, "google")
    pool = session.get(WorkspaceSetting, "pool-access")
    routes = {}
    for route_id in {crew.route_id, *[a.route_id for a in crew.agents]} - {None}:
        current = session.get(ModelRoute, route_id)
        routes[route_id] = (
            [current.provider, current.model, current.credential_id] if current else None
        )
    data = {
        "generation": crew.runtime_generation,
        "version": crew.config_version,
        "responsibilities": crew.responsibilities,
        "schedule": [crew.trigger_type, crew.review_interval_minutes, crew.schedule_time],
        "budget": [crew.max_model_calls, crew.daily_call_limit],
        "draft": draft,
        "permissions": sorted(crew.permissions),
        "route": [route.id, route.provider, route.model, route.credential_id] if route else None,
        "account": [account.subject, sorted(account.scopes)] if account else None,
        "pool": pool.value if pool else {},
        "device_monitor": crew.configuration_plan.get("device_monitor"),
        "actions_paused": runtime.actions.rules.paused(session),
        "connected_accounts": {
            provider: [
                runtime.connectors.subject(provider),
                sorted(runtime.connectors.status(provider).get("capabilities", [])),
                sorted(runtime.connectors.status(provider).get("repositories", [])),
            ]
            for provider in {
                provider_for(op) for op in PREVIEW_ACTIONS if action_grant(op) in crew.permissions
            }
            - {"local"}
        },
        "agents": [[a.id, a.status, a.instructions, a.route_id, a.definition] for a in crew.agents],
        "routes": routes,
        "rules": {
            a["operation"]: runtime.actions.rules.read(session, a["operation"], crew.id)
            for a in [
                {"operation": op} for op in set(MODELS) if action_grant(op) in crew.permissions
            ]
        },
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
