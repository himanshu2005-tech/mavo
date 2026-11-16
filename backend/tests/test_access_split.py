"""Reading what others can write and changing things are two agents, and agents need not share
one model. "Plan my day and add prep tasks" came back as one agent reading the calendar and
writing tasks, with the crew blocked on "Choose an agent connection"."""

from types import SimpleNamespace

from mavo.crew_composition import CrewComposition, separate_access
from mavo.crew_planner import main_route


def one(**agent):
    return CrewComposition.model_validate(
        {
            "agents": [
                {
                    "key": "planner",
                    "name": "Day Block Planner",
                    "role": "Plans tomorrow",
                    "instructions": "Plan tomorrow in time blocks and add missing prep tasks.",
                    "completion_criteria": "A plan.",
                    **agent,
                }
            ]
        }
    )


def test_a_calendar_reader_that_writes_tasks_becomes_a_reader_and_a_planner():
    design = separate_access(
        one(
            capabilities=[
                "google:calendar:read",
                "google:tasks:read",
                "google:tasks:write",
                "notifications:show",
            ],
            actions=["tasks.create"],
            route_id="fast",
        )
    )
    reader, planner = design.agents
    assert reader.capabilities == ["google:calendar:read"] and not reader.actions
    assert "google:calendar:read" not in planner.capabilities
    assert {"google:tasks:read", "google:tasks:write"} <= set(planner.capabilities)
    assert planner.actions == ["tasks.create"] and planner.depends_on == [reader.key]
    assert reader.route_id == planner.route_id == "fast"


def test_one_agent_stays_one_when_there_is_no_reason_to_split():
    reads_only = one(capabilities=["google:gmail:read", "notifications:show"])
    assert len(separate_access(reads_only).agents) == 1
    own_data = one(
        capabilities=["google:tasks:read", "google:tasks:write"], actions=["tasks.create"]
    )
    assert len(separate_access(own_data).agents) == 1  # its own tasks: nobody else writes those
    # It archives, answers or edits the very mail or events it reads: it has to see them.
    organiser = one(
        capabilities=["google:gmail:read", "google:gmail:modify"], actions=["gmail.archive"]
    )
    assert len(separate_access(organiser).agents) == 1
    events = one(
        capabilities=["google:calendar:read", "google:calendar:write"],
        actions=["calendar.update"],
    )
    assert len(separate_access(events).agents) == 1
    on_a_page = one(actions=["browser.open", "browser.read", "browser.click", "browser.fill"])
    assert len(separate_access(on_a_page).agents) == 1  # clicking stays with the page's reader


def test_a_browsing_digest_that_saves_notes_cites_its_pages_from_the_reader():
    design = separate_access(
        one(actions=["browser.search", "notes.write"], verification="listings")
    )
    reader, writer = design.agents
    assert {"browser.open", "browser.read", "browser.search"} <= set(reader.actions)
    assert reader.verification == "listings" and writer.verification == "deliverable"
    assert writer.actions == ["notes.write"]


def test_the_crew_gets_a_connection_without_being_asked_to_choose():
    routes = {
        "local": SimpleNamespace(id="local", provider="ollama"),
        "cloud": SimpleNamespace(id="cloud", provider="airouter"),
        "strong": SimpleNamespace(id="strong", provider="airouter"),
    }
    assert main_route(routes, None) == "cloud"  # a cloud one before a small local model
    design = one(route_id="strong")
    assert main_route(routes, design) == "strong"  # the one its agents use
    assert main_route({}, None) is None


def test_a_reminder_or_device_worker_gets_the_one_capability_it_cannot_work_without():
    """Library "Wear My Watch" and "Sustained CPU Review" came back as one worker without
    notifications:show / device:read, and the whole chat turn failed as "setup changed"."""
    from mavo.crew_composition import deterministic_fit

    reminder = SimpleNamespace(trigger_type="daily", template_id="schedule")
    fitted = deterministic_fit(reminder, one(capabilities=[]))
    assert fitted.agents[0].capabilities == ["notifications:show"]
    monitor = SimpleNamespace(trigger_type="interval", template_id="computer")
    assert "device:read" in deterministic_fit(monitor, one(capabilities=[])).agents[0].capabilities
    two = CrewComposition.model_validate(
        {
            "agents": [
                {
                    "key": k,
                    "name": k,
                    "role": "r",
                    "instructions": "Do it.",
                    "completion_criteria": "Done.",
                }
                for k in ("a", "b")
            ]
        }
    )
    assert deterministic_fit(monitor, two) is None  # several agents: the default worker instead
    other = SimpleNamespace(trigger_type="interval", template_id="research")
    plain = one(capabilities=[])
    assert deterministic_fit(other, plain) is plain


def test_an_evening_planner_at_nine_is_split_even_though_it_starts_as_a_daily_trigger():
    """ "Every evening at 9, read my calendar for tomorrow and my open tasks, then plan my day
    and add missing prep tasks" came back as one agent: the draft arrives as a daily trigger
    and only becomes a once-a-day review after the split check had skipped it."""
    from mavo.schemas import CrewDraft

    draft = CrewDraft.model_validate(
        {
            "template_id": "schedule",
            "name": "Evening Day Planner",
            "responsibilities": "Read tomorrow's calendar and open tasks, plan the day in time "
            "blocks and add missing prep tasks.",
            "trigger_type": "daily",
            "schedule_time": "21:00",
            "requested_notifications": True,
            "composition": {
                "agents": [
                    {
                        "key": "planner",
                        "name": "Day Block Planner",
                        "role": "Plans tomorrow",
                        "instructions": "Plan tomorrow in time blocks and add missing prep tasks.",
                        "capabilities": [
                            "google:calendar:read",
                            "google:tasks:read",
                            "google:tasks:write",
                            "notifications:show",
                        ],
                        "actions": ["tasks.create"],
                        "completion_criteria": "A plan and the missing prep tasks.",
                    }
                ]
            },
        }
    )
    assert draft.trigger_type == "interval" and draft.review_interval_minutes == 1440
    reader, planner = draft.composition.agents
    assert reader.capabilities == ["google:calendar:read"]
    assert planner.depends_on == [reader.key] and planner.actions == ["tasks.create"]


def test_a_plain_daily_reminder_stays_one_text_worker():
    from mavo.schemas import CrewDraft

    draft = CrewDraft.model_validate(
        {
            "template_id": "schedule",
            "name": "Water",
            "responsibilities": "Remind me to drink water.",
            "daily_triggers": [{"time": "10:00", "message": "Drink water."}],
            "requested_notifications": True,
            "composition": {
                "agents": [
                    {
                        "key": "reminder",
                        "name": "Reminder",
                        "role": "Reminds",
                        "instructions": "Show the reminder text.",
                        "capabilities": ["notifications:show"],
                        "completion_criteria": "Shown.",
                    }
                ]
            },
        }
    )
    assert draft.trigger_type == "daily" and len(draft.composition.agents) == 1
