"""Read-only first-run verification using the existing Crew/TaskRunner authorities.

Private test adapters never become user Crew. No write is executed to prove a setup works.
Results and measured call counts are persisted on the source draft, not inferred from prose.
"""

import asyncio
import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from mavo.database import (
    ActionRequest,
    ConversationEvent,
    CrewInstance,
    CrewRun,
    TaskRun,
    WorkspaceSetting,
    utcnow,
)
from mavo.verification_preview import (
    PREVIEW_ACTIONS,
    PREVIEW_PROTOCOL,
    READ_ACTIONS,
    WRITE_GRANTS,
    action_preview_record,
)

logger = logging.getLogger(__name__)
FIRST_RUN_TIMEOUT = 240
BROWSER_FIRST_RUN_TIMEOUT = 420


def is_verification(session, crew):
    if not crew:
        return False
    source_id = crew.configuration_plan.get("first_run_source")
    if not source_id:
        return False
    source = session.get(CrewInstance, source_id)
    record = source.configuration_plan.get("first_run", {}) if source else {}
    return bool(
        source
        and source.status == "draft"
        and record.get("status") == "running"
        and record.get("adapter_id") == crew.id
        and record.get("generation") == source.runtime_generation
        and set(crew.permissions) <= set(source.permissions)
    )


def calibrated_budget(measured, ceiling):
    """Up to three spare calls above what the test used, as far as the approved ceiling allows.
    The work fitting is what matters: an inbox alert that used 2 of its 3 calls once failed setup
    for want of 3 spare ("2 measured + 3 spare = 5")."""
    wanted = measured + 3
    return {
        "measured_calls": measured,
        "recommended_calls": wanted,
        "configured_calls": max(min(wanted, ceiling), min(measured, ceiling)),
        "buffer_complete": wanted <= ceiling,
        "fits": measured <= ceiling,
    }


def cleanup_adapter(session, adapter_id):
    from mavo.crew_workers import stop_crew

    adapter = session.get(CrewInstance, adapter_id)
    if not adapter:
        return 0
    workers = [a.worker_id for a in adapter.agents if a.worker_id]
    measured = (
        session.scalar(
            select(func.coalesce(func.sum(CrewRun.model_calls), 0)).where(
                CrewRun.crew_id.in_([adapter_id, *workers])
            )
        )
        or 0
    )
    stop_crew(session, adapter)
    # Ephemeral previews/read diagnostics belong to this private test, not user action history.
    session.execute(delete(ActionRequest).where(ActionRequest.crew_id.in_([adapter_id, *workers])))
    for agent in adapter.agents:
        if agent.worker_id and agent.worker:
            worker = agent.worker
            agent.worker_id = None
            session.flush()
            session.delete(worker)
    session.delete(adapter)
    for setting in session.scalars(
        select(WorkspaceSetting).where(
            WorkspaceSetting.key.startswith("action:" + adapter_id + ":")
        )
    ).all():
        session.delete(setting)
    return measured


def recover_first_runs(factory):
    """An interrupted test is not a pass, nor permission to repeat paid work on restart."""
    with factory() as session:
        for source in session.scalars(select(CrewInstance).where(CrewInstance.status == "draft")):
            record = dict(source.configuration_plan.get("first_run", {}))
            if record.get("status") != "running":
                continue
            measured = cleanup_adapter(session, record.get("adapter_id", ""))
            if source.budget_day == record.get("budget_day"):
                source.daily_calls_used = max(
                    0, source.daily_calls_used - max(0, record.get("reserved_calls", 0) - measured)
                )
            record.update(
                status="interrupted",
                measured_calls=measured,
                message="Mavo closed during testing. Test again before adding.",
            )
            source.configuration_plan = {**source.configuration_plan, "first_run": record}
        session.commit()


async def verify_once(runtime, crew_id, sample=None):
    from mavo.crew_composition import CrewComposition, set_composition
    from mavo.crew_workers import first_run_at

    adapter_id = str(uuid4())
    run_id = None
    reserved = 0
    day = utcnow().astimezone().date().isoformat()
    record = {}
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        if not source or source.status != "draft":
            raise HTTPException(409, "Only a saved setup draft can be tested")
        previous = source.configuration_plan.get("first_run", {})
        # "starting": the setup card marked it running as it handed the test to the background.
        if (
            previous.get("status") == "running"
            and previous.get("phase") != "starting"
            and not (sample and previous.get("phase") == "writing_sample")
        ):
            raise HTTPException(409, "This setup is already being tested")
        record = {
            "status": "running",
            "adapter_id": adapter_id,
            "generation": source.runtime_generation,
            "started_at": utcnow().isoformat(),
            "budget_day": day,
            # A test on a sample shows the sample from the start, said to be one, while the
            # agents work on it: the person sees what they are being tested with.
            **(
                {"tested_with": "sample", "sample": sample, "sample_note": SAMPLE_NOTE}
                if sample
                else {}
            ),
        }
        source.configuration_plan = {**source.configuration_plan, "first_run": record}
        session.commit()

    try:
        with runtime.factory() as session:
            source = session.get(CrewInstance, crew_id)
            if (
                WRITE_GRANTS & set(source.permissions) or "device:clean_temp" in source.permissions
            ) and (
                getattr(runtime, "first_run_preview_protocol", None) != PREVIEW_PROTOCOL
                or getattr(runtime.actions, "first_run_preview_protocol", None) != PREVIEW_PROTOCOL
            ):
                raise ValueError(
                    "Restart Mavo before testing action previews. "
                    "The running backend does not support safe previews. Nothing was written."
                )
            # A missing permission is not silently dropped to make the test appear successful.
            unsupported = [
                p
                for p in source.permissions
                if (p.startswith("action:") and p[7:] not in READ_ACTIONS | PREVIEW_ACTIONS)
                or (p.endswith((":write", ":send", ":modify")) and p not in WRITE_GRANTS)
            ]
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
            declared |= {p[7:] for p in source.permissions if p.startswith("action:")}
            unsupported.extend(sorted(declared - READ_ACTIONS - PREVIEW_ACTIONS))
            if unsupported:
                raise ValueError(
                    "Unsupported setup capabilities: "
                    + ", ".join(unsupported)
                    + ". No tool was installed or executed. Nothing was sent, changed or deleted."
                )
            # Declared tools must be authorized even if no current input exercises them.
            for operation in declared:
                rule = runtime.actions.rules.read(session, operation, source.id)
                from mavo.connectors.catalog import action_grant

                if action_grant(operation) not in source.permissions or not rule["enabled"]:
                    raise ValueError(
                        "Allow " + operation + " for this Crew before testing. Nothing was changed."
                    )
                from mavo.connectors.catalog import provider_for

                if provider_for(
                    operation
                ) != "local" and not runtime.connectors.capability_available(operation):
                    raise ValueError(
                        "Connect and grant account access for " + operation + " before testing."
                    )
            permissions = [p for p in source.permissions if p != "notifications:show"]
            # Crew consent is a ceiling, not permission for an empty member. Do not
            # certify an inbox/calendar check when nobody can collect its required data.
            required_reads = required_reads_of(source)
            if problem := unread_sources(source):
                raise ValueError(problem)
            if source.trigger_type == "daily":
                # Verify scheduling, never fire an early reminder as a test.
                if first_run_at(source, utcnow()) is None:
                    raise ValueError("No valid next reminder time")
                record.update(
                    status="passed",
                    kind="schedule_validation",
                    measured_calls=0,
                    message="Schedule validated; notification delivery is not yet tested.",
                )
                record.update(action_preview_record(session, runtime, source))
                return record
            composition = source.configuration_plan.get("composition")
            if not composition:
                composition = {
                    "agents": [
                        {
                            "key": "worker",
                            "name": source.name,
                            "role": "Primary worker",
                            "instructions": source.responsibilities[:3000],
                            "capabilities": [p for p in permissions if p != "model:invoke"],
                            "actions": sorted(declared),
                            "max_model_calls": min(source.max_model_calls, 20),
                            "completion_criteria": (
                                "Produce the requested result or report a real blocker."
                            ),
                        }
                    ],
                    "limits": {"model_calls": min(40, max(2, source.max_model_calls))},
                }
            spec = CrewComposition.model_validate(composition)
            for member in spec.agents:
                missing = set(member.capabilities) - set(permissions) - {"notifications:show"}
                if missing:
                    raise ValueError(member.name + " needs access: " + ", ".join(sorted(missing)))
                member.capabilities = [p for p in member.capabilities if p != "notifications:show"]
            # No call budget to fit: limits.RUN_MAX and limits.DAY_MAX are the only ceilings.
            # (Tests were refused or cut short for a draft's small budget: "used 2 of its 12".)
            reserved = 0 if source.template_id == "computer" else spec.limits.model_calls
            used = source.daily_calls_used if source.budget_day == day else 0
            source.budget_day, source.daily_calls_used = day, used + reserved
            record["reserved_calls"] = reserved
            source.configuration_plan = {**source.configuration_plan, "first_run": record}
            adapter = CrewInstance(
                id=adapter_id,
                name="_first_run_" + source.id,
                hidden=True,
                template_id=source.template_id,
                responsibilities=source.responsibilities,
                status="active",
                route_id=source.route_id,
                provider=source.provider,
                model=source.model,
                permissions=permissions,
                resources=list(source.resources),
                trigger_type="manual",
                is_running=True,
                runtime_state="waiting",
                next_run_at=utcnow(),
                max_model_calls=source.max_model_calls,
                daily_call_limit=reserved,
                configuration_plan={
                    "first_run_source": source.id,
                    # The real agent may alert: the test writes its alert as it would, and Mavo
                    # shows it as the test's result instead of popping it up. Told "permission:
                    # False", a quote agent's test reported "Desktop Alert Unavailable" and passed.
                    "test_notifications": "notifications:show" in source.permissions,
                    # A sample input Mavo wrote for this test, read instead of the inbox.
                    **({"sample_inputs": sample} if sample else {}),
                    **(
                        {"device_monitor": source.configuration_plan["device_monitor"]}
                        if source.configuration_plan.get("device_monitor")
                        else {}
                    ),
                },
            )
            session.add(adapter)
            session.flush()
            if source.template_id != "computer":
                set_composition(session, adapter, spec)
            for operation in READ_ACTIONS | PREVIEW_ACTIONS:
                rule = runtime.actions.rules.read(session, operation, source.id)
                if rule["enabled"]:
                    session.add(
                        WorkspaceSetting(
                            key=runtime.actions.rules.key(operation, adapter.id), value=rule
                        )
                    )
            session.commit()
        # Run the same execution machinery as production, under a wall-clock ceiling. A
        # browsing Crew opens and reads several pages (20-40 s each): it gets longer.
        browsing = any(p.startswith("action:browser.") for p in source.permissions)
        async with asyncio.timeout(BROWSER_FIRST_RUN_TIMEOUT if browsing else FIRST_RUN_TIMEOUT):
            await runtime.work(adapter_id)
            while True:
                with runtime.factory() as session:
                    adapter = session.get(CrewInstance, adapter_id)
                    if not is_verification(session, adapter):
                        raise ValueError("Setup changed or access was revoked during verification")
                    run = session.scalar(
                        select(TaskRun)
                        .where(TaskRun.crew_id == adapter_id)
                        .order_by(TaskRun.created_at.desc())
                    )
                    record["last_state"] = adapter.runtime_state
                    if not run and adapter.last_runtime_error:
                        raise ValueError(adapter.last_runtime_error)
                    if run:
                        run_id = run.id
                        pending = session.scalar(
                            select(ConversationEvent).where(
                                ConversationEvent.task_id == run_id,
                                ConversationEvent.status == "pending",
                                ConversationEvent.kind.in_(
                                    ["question", "permission_request", "approval_request"]
                                ),
                            )
                        )
                        if (
                            pending
                            and pending.kind == "question"
                            and record.get("tested_with") == "sample"
                        ):
                            # A question about Mavo's own made-up sample emails ("the Q4
                            # Strategy Sync with Alex") is no question for the person: it was
                            # shown as a setup question about mail they never had. The test
                            # proved the agent works; what it would ask is its result.
                            pending.status = "cancelled"
                            session.commit()
                            record["preview"] = (
                                "On a sample email it would stop and ask you: "
                                + str(pending.payload.get("summary") or "")[:600]
                            )
                            record["asked_on_sample"] = True
                            break
                        if pending:
                            if pending.kind == "question":
                                record["questions"] = [pending.payload["summary"]]
                                record["choices"] = list(pending.payload.get("choices") or [])[:6]
                            raise ValueError(
                                pending.payload.get("summary") or "Setup needs more access"
                            )
                        if run.status != "running":
                            if run.status != "done":
                                raise ValueError(
                                    run.error or "First run did not produce a verified result"
                                )
                            record["preview"] = (run.result or "")[:2500]
                            break
                    elif source.template_id == "computer":
                        if adapter.last_runtime_error:
                            raise ValueError(adapter.last_runtime_error)
                        if not adapter.observation.get("metrics"):
                            raise ValueError("Device metrics were not observed")
                        break
                await asyncio.sleep(0.2)
        with runtime.factory() as session:
            adapter = session.get(CrewInstance, adapter_id)
            workers = [adapter_id, *[a.worker_id for a in adapter.agents if a.worker_id]]
            measured = (
                session.scalar(
                    select(func.coalesce(func.sum(CrewRun.model_calls), 0)).where(
                        CrewRun.crew_id.in_(workers)
                    )
                )
                or 0
            )
            read_checks = [
                check
                for agent in adapter.agents
                if agent.worker
                for check in agent.worker.observation.get("google_read_checks", {}).values()
                if check.get("scope") == run_id
            ]
            expected_sources = {c.split(":")[1] for c in required_reads & set(source.permissions)}
            unobserved = expected_sources - {check["resource"] for check in read_checks}
            if unobserved:
                raise ValueError(
                    "The test did not read required sources: "
                    + ", ".join(sorted(unobserved))
                    + ". A model response alone is not a verified source check."
                )
            if "google:gmail:body" in required_reads & set(source.permissions):
                mail_reads = [check for check in read_checks if check["resource"] == "gmail"]
                if any(check.get("supplied_item_count", 0) > 0 for check in mail_reads) and not any(
                    check.get("message_bodies_read", 0) > 0 for check in mail_reads
                ):
                    raise ValueError(
                        "The test read inbox headers but did not read "
                        "the required message contents."
                    )
            if source.template_id == "computer":
                record.update(
                    status="passed",
                    kind="device_metrics",
                    measured_calls=0,
                    preview=(adapter.checkpoint or "")[:2500],
                    message="Device counters read successfully; no files changed.",
                )
                record.update(action_preview_record(session, runtime, source, workers))
                return record
            members = {}
            for agent in adapter.agents:
                n = (
                    session.scalar(
                        select(func.coalesce(func.sum(CrewRun.model_calls), 0)).where(
                            CrewRun.crew_id == agent.worker_id
                        )
                    )
                    or 0
                )
                members[agent.key] = calibrated_budget(n, min(source.max_model_calls, 20))
            # How many calls the test used is recorded, never a reason to fail it.
            used = sum(value["measured_calls"] for value in members.values()) or measured
            cycle_calls = sum(value["configured_calls"] for value in members.values())
            if not members:
                cycle_calls = measured + 3
            cycle_calls = max(used, cycle_calls)
            calibration = calibrated_budget(measured, source.max_model_calls)
            if members:
                calibration["configured_calls"] = max(
                    v["configured_calls"] for v in members.values()
                )
                calibration["recommended_calls"] = max(
                    v["recommended_calls"] for v in members.values()
                )
                calibration["buffer_complete"] = True
            record.update(
                status="passed",
                kind="read_only_execution",
                **calibration,
                cycle_calls=cycle_calls,
                agents=members,
                message="Read-only first run completed and verified.",
            )
            record.update(action_preview_record(session, runtime, source, workers))
    except asyncio.CancelledError:
        record.update(
            status="interrupted", message="Verification was interrupted; no Crew was added."
        )
        raise
    except (ValueError, TimeoutError) as exc:
        message = str(exc) or "First-run verification timed out"
        reason, _, partial = message.partition("\nPartial source results")
        if partial:
            record["preview"] = "Partial source results" + partial[:2500]
        record.update(status="failed", message=reason[:1500])
    except Exception:
        logger.exception("First-run verification failed for %s", crew_id)
        record.update(
            status="failed", message="First-run verification failed. See the backend log."
        )
    finally:
        if not run_id:
            with runtime.factory() as session:
                run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == adapter_id))
        if run_id:
            runtime.task_runner.stop(run_id)
        with runtime.factory() as session:
            adapter = session.get(CrewInstance, adapter_id)
            if adapter:
                checks = {
                    agent.key: agent.worker.observation["feed_checkpoint"]
                    for agent in adapter.agents
                    if agent.worker and agent.worker.observation.get("feed_checkpoint")
                }
                if checks:
                    # Keep only application-generated counts/indexes, never raw model
                    # answers or source text, after disposable workers are removed.
                    record["feed_checks"] = checks
                resource_checks = {
                    agent.key: [
                        check
                        for check in agent.worker.observation.get("google_read_checks", {}).values()
                        if check.get("scope") == run_id
                    ]
                    for agent in adapter.agents
                    if agent.worker
                }
                if any(resource_checks.values()):
                    record["resource_checks"] = resource_checks
            measured = cleanup_adapter(session, adapter_id)
            source = session.get(CrewInstance, crew_id)
            if source and source.runtime_generation == record.get("generation"):
                if reserved and source.budget_day == day:
                    source.daily_calls_used = max(
                        0, source.daily_calls_used - max(0, reserved - measured)
                    )
                record["measured_calls"] = measured
                record["finished_at"] = utcnow().isoformat()
                source.configuration_plan = {**source.configuration_plan, "first_run": record}
            session.commit()
    return record


# ---- a test with a sample, when the person's own data gave the Crew nothing to do -----------

# What each source's items look like when Mavo reads them, for the model writing a sample.
SAMPLE_SHAPES = {
    "gmail": '{"sender": "Name <name@example.com>", "subject": "...", "date": "RFC 2822 date", '
    '"text": "the whole email"}',
    "calendar": '{"summary": "...", "status": "confirmed", "start": {"dateTime": "ISO with '
    'offset"}, "end": {"dateTime": "ISO with offset"}, "location": "..."}',
    "tasks": '{"title": "...", "notes": "...", "due": "YYYY-MM-DDT00:00:00.000Z", '
    '"status": "needsAction"}',
    "drive": '{"name": "...", "mimeType": "...", "modifiedTime": "ISO"}',
    "contacts": '{"name": "...", "email": "name@example.com", "phone": "..."}',
}
# Said with every sample, wherever it is shown.
SAMPLE_NOTE = (
    "This is a sample Mavo wrote only for this test, to see how the agents behave. It is not "
    "real, and nothing is sent or changed."
)
EMAIL_ADDRESS = r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)"


def sample_sources(source) -> list[str]:
    """The sources a Crew reads that a sample can stand in for: the person's own accounts
    (mail, calendar, tasks, files, contacts). News feeds are left out: they nearly always have
    something new, and a feed's stories are checked against the exact items it gave."""
    draft = (source.configuration_plan or {}).get("draft") or {}
    reads = set(draft.get("requested_google_resources") or [])
    reads |= {
        c.split(":")[0]
        for c in draft.get("requested_google_capabilities") or []
        if c.endswith(":read")
    }
    reads |= {
        p.split(":")[1]
        for p in source.permissions
        if p.startswith("google:") and p.endswith(":read")
    }
    return sorted(r for r in reads if r in SAMPLE_SHAPES)


def needs_sample(record: dict, sources: list[str]) -> bool:
    """The real test gave the Crew nothing to do: it passed without trying any action of its,
    or its whole result was Mavo's own quiet "Nothing new." (an empty inbox, a free calendar),
    or it stopped on a question (not a missing permission), which empty data often causes: a
    reader that found only holidays asked for calendar access it had. If the sample run asks
    too, the question stands."""
    if not sources or record.get("tested_with"):
        return False
    if record.get("status") == "failed" and record.get("questions"):
        return True
    if record.get("status") != "passed":
        return False
    untried = record.get("kind") == "action_preview" and not record.get("proposed_actions")
    quiet = not record.get("proposed_actions") and (
        record.get("preview") or ""
    ).strip().lower() in {
        "nothing new",
        "nothing new.",
    }
    return bool(untried and record.get("unexercised_actions")) or quiet


def arrived(date) -> str:
    """A sample email's date, never after now: it has arrived. One dated tomorrow was taken as
    not received yet, and the test tested nothing."""
    from datetime import timedelta
    from email.utils import format_datetime, parsedate_to_datetime

    now = utcnow().astimezone()
    try:
        when = parsedate_to_datetime(str(date))
    except (TypeError, ValueError):
        when = None
    if when is None or when.tzinfo is None or when > now:
        when = now - timedelta(minutes=10)
    return format_datetime(when)


def safe_sample(value, sources: list[str]) -> dict:
    """The model's sample, made impossible to mistake for real data or to reach anyone: only the
    Crew's own sources, at most three items each, every id "sample-", every address and link on
    example.com (a domain that receives no mail)."""
    import json
    import re

    out = {}
    if not isinstance(value, dict):
        return out
    for resource, items in value.items():
        if resource not in sources or not isinstance(items, list):
            continue
        kept = []
        for index, item in enumerate(items[:3]):
            if not isinstance(item, dict):
                continue
            text = json.dumps(item, ensure_ascii=False)
            text = re.sub(
                EMAIL_ADDRESS, lambda m: m.group(0).replace(m.group(1), "example.com"), text
            )
            text = re.sub(r"https?://[^\s\"/]+", "https://example.com", text)
            item = json.loads(text)
            item.update(id=f"sample-{resource.replace(':', '-')}-{index + 1}", sample=True)
            if resource == "gmail":
                item.setdefault("thread_id", item["id"])
                item["labels"] = ["INBOX", "UNREAD", "SAMPLE"]
                item["date"] = arrived(item.get("date"))
            kept.append(item)
        if kept:
            out[resource] = kept
    return out


async def write_sample(runtime, crew_id) -> dict:
    """One realistic input this Crew exists to handle, written by the model from the Crew's own
    job (never a fixed template): an email asking for a meeting for a meeting booker, a clashing
    event for a calendar guard, a task due tomorrow for a task reminder. {} when it cannot."""
    from mavo.database import ModelRoute
    from mavo.recipes import parse_json
    from mavo.tasks import now_line
    from mavo.thinking import split_thinking

    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        if not source:
            return {}
        sources = sample_sources(source)
        roles = [
            f"{a.name}: {(a.definition or {}).get('instructions') or a.instructions or ''}"[:300]
            for a in source.agents
            if a.status == "active"
        ]
        route = session.get(ModelRoute, source.route_id) if source.route_id else None
        job = source.responsibilities
    if not sources:
        return {}
    if route is None:
        try:
            route = runtime.task_runner.models()[0]
        except Exception:  # noqa: BLE001
            return {}
    shapes = "\n".join(f"- {r}: {SAMPLE_SHAPES[r]}" for r in sources)
    prompt = (
        f"{now_line()}A Crew is being tested before it is turned on, but the person's own data "
        "gave it nothing to do. Write a SAMPLE input it exists to handle, so its behaviour can "
        f"be checked.\nTHE CREW'S JOB: {job[:1500]}\n"
        + (f"ITS AGENTS: {' | '.join(roles)[:1500]}\n" if roles else "")
        + f"ITS SOURCES, and what an item of each looks like:\n{shapes}\n\n"
        "Reply with ONLY a JSON object: EVERY source listed -> a list of 1 to 3 items, so each "
        "thing the Crew reads is tested (a meeting booker gets an email asking for a meeting AND "
        "a calendar with a few busy times, so its choice of slot is tested too). When the "
        "email offers times, make the FIRST offered time busy in the calendar (an event "
        "overlapping it) and a later one free: the test then shows the Crew skips the busy "
        "time and takes the next free one (a booker took a busy 11:00 because its test "
        "never had a clash). Make at least "
        "one item that should make the Crew do its job, dated so it would act on it if it ran "
        "at NOW (its 'tomorrow' is the day after NOW). An email has already arrived: its own "
        "date is a little before NOW, never after it; only the times it asks for are later (a "
        "sample email dated tomorrow was taken as not received yet, and nothing was tested). "
        "Use only names and addresses at example.com and links on example.com."
    )
    try:
        raw = await asyncio.wait_for(
            runtime.router.chat(
                route.provider,
                route.model,
                [{"role": "user", "content": prompt}],
                "You write realistic sample data for testing. JSON only.",
                getattr(route, "credential_id", None),
            ),
            timeout=120,
        )
        return safe_sample(parse_json(split_thinking(str(raw))[0]), sources)
    except Exception:  # noqa: BLE001
        logger.info("Could not write a sample for crew %s", crew_id, exc_info=True)
        return {}


def sample_for(session, crew) -> dict:
    """The sample a test adapter (or one of its member workers) reads instead of the real
    source; {} for every real run."""
    from mavo.crew_composition import execution_agent

    if crew is None:
        return {}
    plan = crew.configuration_plan or {}
    if plan.get("sample_inputs"):
        return plan["sample_inputs"]
    member = execution_agent(session, crew.id)
    owner = session.get(CrewInstance, member.crew_id) if member else None
    return ((owner.configuration_plan or {}).get("sample_inputs") or {}) if owner else {}


def sample_line(sample: dict) -> str:
    """'a sample email from Ananya <ananya@example.com>: Interview': what it was tested with."""
    parts = []
    for resource, items in sample.items():
        name = {"gmail": "email", "calendar": "event", "tasks": "task", "contacts": "contact"}.get(
            resource, "item"
        )
        for item in items:
            title = (
                item.get("subject")
                or item.get("summary")
                or item.get("title")
                or item.get("name")
                or ""
            )
            who = f" from {item['sender']}" if item.get("sender") else ""
            parts.append(f"a sample {name}{who}: {title}"[:200])
    return "; ".join(parts)


def required_reads_of(source) -> set[str]:
    """The Google reads the setup asked for, as grants ("google:gmail:read")."""
    draft = (source.configuration_plan or {}).get("draft", {})
    reads = {
        "google:" + c
        for c in draft.get("requested_google_capabilities", [])
        if c.endswith((":read", ":body"))
    }
    return reads | {"google:" + r + ":read" for r in draft.get("requested_google_resources", [])}


def unread_sources(source) -> str | None:
    """Why the setup cannot be tested: a source it needs that none of its agents may read."""
    members = [a for a in source.agents if a.status == "active"]
    if not members:
        return None
    assigned = {c for a in members for c in (a.definition or {}).get("capabilities", [])}
    missing_reads = (required_reads_of(source) & set(source.permissions)) - assigned
    if not missing_reads:
        return None
    return (
        "Crew access is saved, but no active member can read required sources: "
        + ", ".join(sorted(missing_reads))
        + ". Refine role access before testing; no source check was verified."
    )


SAMPLE_FIRST_NOTE = "Mavo tested it with {line}. It never tests on your real mail. " + SAMPLE_NOTE


async def sample_first(runtime, crew_id):
    """The setup test of a Crew that reads mail: on a sample Mavo writes for it, with a busy
    time among the offered ones; nothing real is read or changed."""

    def save(value):
        with runtime.factory() as session:
            source = session.get(CrewInstance, crew_id)
            if source:
                source.configuration_plan = {**source.configuration_plan, "first_run": value}
                session.commit()

    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        problem = unread_sources(source) if source else None
    if not problem:
        # Access is the Google connection's own grant, read from it: no mail is opened.
        pool = getattr(runtime, "google_pool", None)
        try:
            granted = (pool.auth.status().get("capabilities") or []) if pool else []
        except Exception:  # noqa: BLE001
            granted = []
        if "gmail:read" not in granted:
            problem = "Connect Google and allow reading Gmail, then test it again."
    if problem:  # said before any sample is written: no model call for a setup that cannot run
        failed = {"status": "failed", "message": problem}
        save(failed)
        return failed
    save(
        {
            "status": "running",
            "phase": "writing_sample",
            "message": "Writing a sample email to test it with…",
        }
    )
    sample = await write_sample(runtime, crew_id)
    if not sample:
        failed = {
            "status": "failed",
            "message": "Mavo could not write a sample email to test it with, and it never "
            "tests on your real mail. Test it again, or skip the test.",
        }
        save(failed)
        return failed
    tried = await verify_once(runtime, crew_id, sample)
    tried.update(
        tested_with="sample",
        sample=sample,
        sample_note=SAMPLE_FIRST_NOTE.format(line=sample_line(sample))[:700],
    )
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        if source and source.runtime_generation == tried.get("generation"):
            source.configuration_plan = {**source.configuration_plan, "first_run": tried}
            session.commit()
    return tried


async def verify_first_run(runtime, crew_id):
    """The setup test. A Crew that reads mail is tested on a sample only (sample_first). Any
    other on the person's own data first; when that gave it nothing to do (a free calendar),
    once more with a sample Mavo writes for it, said plainly to be a sample. Nothing is
    changed either way: a test only previews."""
    from mavo.workflow_write import setup_test, wants_workflow

    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        sources = sample_sources(source) if source else []
        workflow = bool(source) and wants_workflow(runtime, source)
    if workflow:
        # One that sends, books or writes: Mavo Core writes its whole path and its scenarios,
        # and it is kept only when every scenario does what it should.
        return await setup_test(runtime, crew_id)
    if "gmail" in sources:
        # Mail is tested on a sample only, never the person's own inbox: the test is for how
        # the agents behave, and a real email has no place in it.
        return await sample_first(runtime, crew_id)
    record = await verify_once(runtime, crew_id)
    if not needs_sample(record, sources):
        return record

    def saved(value):
        with runtime.factory() as session:
            source = session.get(CrewInstance, crew_id)
            if source and source.runtime_generation == record.get("generation"):
                source.configuration_plan = {**source.configuration_plan, "first_run": value}
                session.commit()

    # Still testing: the card says a sample is being written, never "passed, nothing to do".
    saved(
        {
            **record,
            "status": "running",
            "phase": "writing_sample",
            "message": "Nothing in your account needed this right now. Writing a sample to "
            "test it with.",
        }
    )
    sample = await write_sample(runtime, crew_id)
    if not sample:
        saved(record)
        return record
    tried = await verify_once(runtime, crew_id, sample)
    tried.update(
        tested_with="sample",
        sample=sample,
        sample_note=(
            "Nothing in your account needed this Crew right now, so Mavo tested it with "
            + sample_line(sample)
            + ". "
            + SAMPLE_NOTE
        )[:700],
        real_result=(record.get("preview") or "Nothing new.")[:600],
    )
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        if source and source.runtime_generation == tried.get("generation"):
            source.configuration_plan = {**source.configuration_plan, "first_run": tried}
            session.commit()
    return tried
