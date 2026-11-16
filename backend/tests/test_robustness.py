"""What a live run of real agents turned up (2026-11-11), each fixed and kept fixed here:
a mail watcher that went deaf for good after one terse model reply; mail that hides a web page in
its plain-text part; agents that could not tell the weather where the person is; holidays and
other calendars that agents never saw; Gmail history that expired while Mavo was off; and a
forbidden action with a bad date failing a run instead of being refused."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mavo.crew_composition import RUN_RETRIES, finish_cycle
from mavo.crew_workers import parse_checkpoint
from mavo.database import CrewInstance, WorkspaceSetting, utcnow
from mavo.mail_reviews import MailDecision
from mavo.resources.crew_weather import WeatherLookup, saved_place_weather
from mavo.resources.google import GmailHistoryExpired, GoogleResourcePool
from mavo.resources.mail_text import message_text
from tests.test_crew_workers import add_crew, factory  # noqa: F401


def test_a_terse_mail_decision_is_filled_in_not_rejected():
    quiet = MailDecision(
        message_id="m1", important=False, reason="Policy update from a no-reply sender", summary=""
    )
    assert quiet.summary == "Policy update from a no-reply sender"
    loud = MailDecision(
        message_id="m2", important=True, reason="", summary="Pick up relatives at 1 PM tomorrow."
    )
    assert loud.reason == "Pick up relatives at 1 PM tomorrow."
    assert MailDecision(message_id="m3", important=False).summary == "Nothing for you to do."


def failed_run(crew_id, error="The model returned an invalid work checkpoint"):
    return SimpleNamespace(
        id="run",
        crew_id=crew_id,
        crew_generation=1,
        status="failed",
        error=error,
        result=None,
        plan={"trigger_context": {"cursor": 9}},
        created_at=utcnow(),
    )


def test_a_failed_run_is_tried_again_and_only_a_run_of_failures_stops_the_agent(factory):  # noqa: F811
    add_crew(
        factory,
        trigger_type="gmail_message",
        observation={"pending_google": {"events": [{"id": "m1"}]}},
    )
    for attempt in range(1, RUN_RETRIES + 1):
        with factory() as session:
            crew = session.get(CrewInstance, "crew")
            finish_cycle(session, failed_run("crew"))
            session.commit()
            if attempt < RUN_RETRIES:
                assert crew.runtime_state == "waiting", attempt  # not deaf: it comes back
                assert crew.next_run_at > utcnow() + timedelta(seconds=60 * 2**attempt - 5)
                assert "Trying again in" in crew.last_runtime_error
                assert crew.observation["pending_google"]  # the same mail is retried, not skipped
            else:
                assert crew.runtime_state == "error"
    with factory() as session:  # a success clears the count
        crew = session.get(CrewInstance, "crew")
        finish_cycle(
            session,
            SimpleNamespace(
                **{**vars(failed_run("crew")), "status": "done", "error": None, "result": "ok"}
            ),
        )
        assert crew.observation["run_failures"] == 0


def b64(text):
    import base64

    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_a_web_page_in_the_plain_text_part_is_read_as_html():
    page = (
        "<!doctype html><html><head><style>p{}</style></head>"
        "<body><p>Register by Friday.</p></body></html>"
    )
    text, kind = message_text({"mimeType": "text/plain", "body": {"data": b64(page)}})
    assert text == "Register by Friday." and kind == "html"
    plain, kind = message_text(
        {"mimeType": "text/plain", "body": {"data": b64("Pick me up at 1 < 2 pm")}}
    )
    assert plain == "Pick me up at 1 < 2 pm" and kind == "plain"  # a real plain mail is untouched


def test_weather_here_means_the_saved_place_and_never_its_coordinates(factory):  # noqa: F811
    assert WeatherLookup(here=True).here
    with pytest.raises(ValueError):
        WeatherLookup(here=True, query="Delhi")
    place = {
        "source": "device",
        "name": "Current location",
        "latitude": 17.8,
        "longitude": 78.7,
        "region": "",
        "country": "",
        "country_code": "",
    }

    class Adapter:
        async def read_location(self, location):
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "place": {"name": location.name, "latitude": 17.8, "longitude": 78.7},
                    "today": {"max": 28},
                }
            )

    with factory() as session:
        session.add_all(
            [
                WorkspaceSetting(key="weather-location", value=place),
                WorkspaceSetting(
                    key="default-resource-access", value={"weather": True, "location": True}
                ),
            ]
        )
        session.commit()
    out = asyncio.run(saved_place_weather(factory, Adapter()))
    assert out["status"] == "ok" and "latitude" not in json.dumps(out)
    with factory() as session:  # location switched off in Resources: no forecast for "here"
        session.get(WorkspaceSetting, "default-resource-access").value = {
            "weather": True,
            "location": False,
        }
        session.commit()
    out = asyncio.run(saved_place_weather(factory, Adapter()))
    assert out["status"] == "unavailable" and "Location is off" in out["error"]


def pool_with(responses):
    pool = object.__new__(GoogleResourcePool)

    async def get(resource, path, params=None, capability="read"):
        answer = responses(path, params or {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    pool.get = AsyncMock(side_effect=get)
    pool.mail_metadata = AsyncMock(
        side_effect=lambda mid: {
            "id": mid,
            "internal_date": {"old": "100", "new": "300"}[mid],
            "labels": ["INBOX"],
        }
    )
    return pool


def test_expired_gmail_history_starts_afresh_and_reports_only_what_is_new():
    def responses(path, params):
        if path == "/users/me/history":
            return GmailHistoryExpired("gone")
        if path == "/users/me/profile":
            return {"historyId": "999"}
        return {"messages": [{"id": "new"}, {"id": "old"}]}

    items, cursor, events = asyncio.run(
        pool_with(responses).fetch_gmail(
            {"history_id": "5", "snapshot": [{"id": "old", "internal_date": "200"}]}
        )
    )
    assert cursor == {"history_id": "999"}
    assert [e[0] for e in events] == ["new"]  # not "old" again, and no "reconnect Google"


def test_other_switched_on_calendars_are_read_and_labelled_read_only():
    def responses(path, params):
        if path == "/calendars/primary/events":
            return {"items": [{"id": "e1", "summary": "Mine"}]}
        if path == "/users/me/calendarList":
            return {
                "items": [
                    {"id": "primary@x", "primary": True, "selected": True},
                    {
                        "id": "en.indian#holiday@group.v.calendar.google.com",
                        "summary": "Holidays in India",
                        "selected": True,
                    },
                    {"id": "hidden@x", "summary": "Hidden", "selected": False},
                ]
            }
        if "holiday" in path:
            return {"items": [{"id": "h1", "summary": "Bhai Duj"}]}
        raise AssertionError(path)

    items = asyncio.run(pool_with(responses).fetch_calendar())
    assert [i["summary"] for i in items] == ["Mine", "Bhai Duj"]
    assert (
        items[1]["calendar"] == "Holidays in India"
        and items[1]["read_only"]
        and "calendar" not in items[0]
    )


def test_a_forbidden_action_with_a_bad_date_is_refused_not_a_broken_reply():
    reply = json.dumps(
        {
            "state": "wait_for_next_review",
            "summary": "Made it.",
            "actions": [
                {"operation": "tasks.create", "arguments": {"title": "x", "due": "tomorrow-ish"}}
            ],
        }
    )
    # An agent that can do other things: kept, so the permission check refuses it and asks.
    checkpoint = parse_checkpoint(reply, allowed={"gmail.draft": {}})
    assert checkpoint.actions[0].operation == "tasks.create"
    with pytest.raises(ValueError):
        parse_checkpoint(reply, allowed={"tasks.create": {}})  # allowed: its date must be right
    # An agent that may not act at all: dropped, and its report says so instead of claiming it.
    quiet = parse_checkpoint(reply, allowed={})
    assert quiet.actions == [] and "did not do the 1 action" in quiet.summary
    # Words around the JSON keep the same rule (that path re-reads the reply).
    wrapped = parse_checkpoint(
        "Here you go:\n```json\n" + reply + "\n```", allowed={"gmail.draft": {}}
    )
    assert wrapped.actions[0].operation == "tasks.create"


def test_expired_gmail_history_with_nothing_to_compare_calls_no_old_mail_new():
    def responses(path, params):
        if path == "/users/me/history":
            return GmailHistoryExpired("gone")
        if path == "/users/me/profile":
            return {"historyId": "999"}
        return {"messages": [{"id": "new"}, {"id": "old"}]}

    _, cursor, events = asyncio.run(pool_with(responses).fetch_gmail({"history_id": "5"}))
    assert cursor == {"history_id": "999"} and events == []


def review(decisions):
    from mavo.crew_workers import WorkCheckpoint

    return WorkCheckpoint(
        state="wait_for_next_review", summary="Reviewed.", mail_decisions=decisions
    )


MAILS = [
    {"id": "a", "sender": "Ace <ace@x.com>", "subject": "Pick up relatives at 1 PM"},
    {"id": "b", "sender": "Shop <no-reply@shop.com>", "subject": "50% off"},
    {"id": "c", "sender": "Bank <alerts@bank.com>", "subject": "Card used abroad"},
]


def decision(key, important=False):
    return MailDecision(message_id=key, important=important, reason="r", summary="s")


def test_a_mail_left_out_is_named_in_the_repair_request():
    from mavo.mail_reviews import MailReviewError, validate_review

    with pytest.raises(MailReviewError) as raised:
        validate_review(review([decision("a", True), decision("b")]), MAILS)
    assert "left out 1 of the 3" in str(raised.value) and "Card used abroad" in str(raised.value)


def test_extra_and_repeated_decisions_are_dropped_not_a_failure():
    from mavo.mail_reviews import validate_review

    checkpoint = review(
        [decision("a"), decision("a", True), decision("zzz"), decision("b"), decision("c")]
    )
    validate_review(checkpoint, MAILS)
    assert [d.message_id for d in checkpoint.mail_decisions] == ["a", "b", "c"]


def test_after_the_repair_a_mail_still_left_out_is_shown_not_dropped_or_failed():
    from mavo.mail_reviews import validate_review

    checkpoint = review([decision("a", True), decision("b")])
    validate_review(checkpoint, MAILS, fill=True)
    filled = checkpoint.mail_decisions[-1]
    assert filled.message_id == "c" and filled.important and "Card used abroad" in filled.summary


def test_a_reply_with_no_decisions_or_a_planners_gap_still_fails_and_is_retried():
    from mavo.mail_reviews import MailReviewError, validate_review

    with pytest.raises(MailReviewError):  # flagging every mail as important would be noise
        validate_review(review([]), MAILS, fill=True)
    # A planner's gap after the repair: shown as not scheduled, never guessed into the calendar.
    planner = review([decision("a"), decision("b")])
    validate_review(planner, MAILS, planning=True, fill=True)
    assert planner.mail_decisions[-1].follow_up == "none"
    assert planner.mail_decisions[-1].summary.startswith("Not scheduled")


def test_a_planner_that_leaves_an_important_mail_unresolved_shows_it_instead_of_failing():
    from mavo.mail_reviews import MailReviewError, validate_review

    unresolved = review([decision("a", True), decision("b"), decision("c")])
    with pytest.raises(MailReviewError):  # first: the repair request says what is wrong
        validate_review(unresolved, MAILS, planning=True)
    validate_review(unresolved, MAILS, planning=True, fill=True)  # after the repair
    shown = unresolved.mail_decisions[0]
    assert (
        shown.important and shown.follow_up == "none" and shown.summary.startswith("Not scheduled")
    )
    validate_review(unresolved, MAILS, planning=True)  # the final strict check accepts it
    asking = review(
        [
            MailDecision(
                message_id="a",
                important=True,
                reason="r",
                summary="s",
                follow_up="needs_details",
                question="What time is the call?",
            ),
            decision("b"),
            decision("c"),
        ]
    )
    validate_review(asking, MAILS, planning=True, fill=True)
    assert asking.question == "What time is the call?"  # a real question still reaches the person


def test_a_mail_alert_is_plain_short_and_quotes_no_mail_text():
    """It read as nonsense: an internal agent name, a "What needs attention" label on a mail
    that needed nothing, and raw mail cut mid-word with quote marks through it."""
    from mavo.mail_reviews import important_report

    mails = [
        {
            "id": "a",
            "sender": '"P&G Workday System" <pgworkdaysystem.im@pg.com>',
            "subject": "P&G Careers - Application Outcome",
        }
    ]
    summary = "Your IT Intern application was withdrawn; the role closed. Nothing to do."
    checkpoint = review(
        [MailDecision(message_id="a", important=True, reason="Application update", summary=summary)]
    )
    body = "Asha ,\nFirst, we genuinely thank you for the time you have invested"
    text = important_report(checkpoint, mails, [{"id": "a", "body": body}])
    assert text == "P&G Workday System: P&G Careers - Application Outcome\n" + summary
    for mark in ("###", "**", "> ", "excerpt", "What needs attention", "genuinely"):
        assert mark not in text
