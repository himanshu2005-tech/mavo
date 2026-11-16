"""The agent asks the person, on the desktop, with choices and a box for their own words; their
answer carries that same email on, through the same checked actions. And each conversation is
remembered as it went: what they wrote, what was replied, what the person said."""

from mavo.database import CrewInstance, ReminderEvent
from tests.test_desk import Desk, at, did, found, mail, spoken


def asks_first(prompt):
    steps = did(prompt)
    if "find_free_times" not in steps:
        return {"tool": "find_free_times", "args": {"offered": [{"start": at(14), "end": at(15)}]}}
    return {
        "tool": "ask_you",
        "args": {
            "question": "Their 2 PM is busy. What should I do?",
            "choices": ["Offer other times", "Ask them to pick", "Offer other times"],
        },
    }


def the_alert(desk):
    with desk.runtime.factory() as session:
        [alert] = session.query(ReminderEvent).filter_by(kind="question").all()
        return alert.id, alert.message, alert.status


def test_a_question_reaches_the_desktop_with_its_choices(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Ravi <ravi@example.com>", "Roadmap", "Can we meet at 2 PM?")]
    result = desk.run(asks_first)
    assert "Needs you" in result
    alert_id, message, status = the_alert(desk)
    assert message == "Ravi: Roadmap: Their 2 PM is busy. What should I do?"
    [due] = [
        r for r in client.get("/api/v1/reminders/due", headers=auth).json() if r["id"] == alert_id
    ]
    assert due["kind"] == "question"
    assert due["choices"] == ["Offer other times", "Ask them to pick"]  # the same one once


def test_the_answer_carries_the_same_email_on_with_no_new_mail(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Ravi <ravi@example.com>", "Roadmap", "Can we meet at 2 PM?")]
    desk.run(asks_first)
    alert_id, _, _ = the_alert(desk)

    answered = client.post(
        f"/api/v1/reminders/{alert_id}/answer", json={"answer": "Offer other times"}, headers=auth
    )
    assert answered.status_code == 204
    again = client.post(f"/api/v1/reminders/{alert_id}/answer", json={"answer": "x"}, headers=auth)
    assert again.status_code == 409  # answered once
    assert the_alert(desk)[2] == "dismissed"
    with desk.runtime.factory() as session:
        crew = session.get(CrewInstance, desk.crew_id)
        assert crew.runtime_state == "waiting" and crew.next_run_at is not None  # woken now

    desk.mails = []  # nothing new arrived: woken by the answer alone
    seen = []

    def carries_on(prompt):
        seen.append(prompt)
        assert "THEY ANSWERED" in prompt and "Offer other times" in prompt
        assert "Can we meet at 2 PM?" in prompt  # the same email
        steps = did(prompt)
        if "find_free_times" not in steps:
            return {
                "tool": "find_free_times",
                "args": {"offered": [{"start": at(14), "end": at(15)}]},
            }
        if "gmail.send" not in steps:
            first = found(prompt)["alternatives"][0]
            return {
                "tool": "gmail.send",
                "args": {"body": f"Could you do {spoken(first['start'])}?"},
            }
        return {"tool": "done", "args": {"summary": "Offered other times."}}

    result = desk.run(carries_on)
    assert seen, "the agent did not run on the answer"
    assert [op for op, _ in desk.made] == ["gmail.send"]  # through the same checked action
    assert desk.made[0][1]["to"] == "ravi@example.com"
    assert "Ravi · Roadmap\n- Replied: “Could you do" in result
    memory = desk.memory()
    assert memory.get("desk_asks") == {}  # asked no more
    log = memory["desk_memory"]["t1"]["log"]
    assert {
        "you_were_asked": "Their 2 PM is busy. What should I do?",
        "you_answered": "Offer other times",
    } in log
    assert any("we_wrote" in e for e in log)
    # And once carried on, it is not carried on again.
    assert desk.run(carries_on) == "Nothing new."


def test_an_answer_cannot_make_it_book_a_busy_time(client, auth, monkeypatch):
    """The person's words steer the agent; the checks stay: their 2 PM is still busy."""
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Ravi <ravi@example.com>", "Roadmap", "Can we meet at 2 PM?")]
    desk.run(asks_first)
    alert_id, _, _ = the_alert(desk)
    client.post(
        f"/api/v1/reminders/{alert_id}/answer", json={"answer": "Just book 2 PM"}, headers=auth
    )
    desk.mails = []

    def obeys(prompt):
        if "calendar.create" not in did(prompt):
            return {
                "tool": "calendar.create",
                "args": {"summary": "x", "start": at(14), "end": at(15)},
            }
        return {"tool": "done", "args": {"summary": "ok"}}

    desk.run(obeys)
    assert desk.made == []


def test_the_conversation_is_remembered_as_it_went(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Kabir <kabir@example.net>", "Call", "Can we talk this week?")]

    def offers(prompt):
        steps = did(prompt)
        if "find_free_times" not in steps:
            return {"tool": "find_free_times", "args": {}}
        if "gmail.send" not in steps:
            first = found(prompt)["alternatives"][0]
            return {"tool": "gmail.send", "args": {"body": f"How about {spoken(first['start'])}?"}}
        return {"tool": "done", "args": {"summary": "Offered."}}

    desk.run(offers)
    desk.mails = [
        mail(2, "Kabir <kabir@example.net>", "Re: Call", "Hmm, let me check.", thread="t1")
    ]
    seen = []

    def reads(prompt):
        seen.append(prompt)
        return {"tool": "done", "args": {"summary": "Waiting for Kabir."}}

    desk.run(reads)
    conversation = seen[0].split("THIS CONVERSATION SO FAR:")[1]
    assert "Can we talk this week?" in conversation  # what they wrote first
    assert "How about" in conversation  # what was replied
    assert "Hmm, let me check." in conversation  # and now

    # A new thread from the same person: the earlier one is in view.
    desk.mails = [mail(3, "Kabir <kabir@example.net>", "Another thing", "Quick question.")]
    seen.clear()
    desk.run(reads)
    assert "YOUR EARLIER CONVERSATIONS WITH THIS SENDER" in seen[0]
    assert (
        "Can we talk this week?" in seen[0].split("YOUR EARLIER CONVERSATIONS WITH THIS SENDER")[1]
    )


def test_an_old_question_about_the_same_email_is_replaced_by_a_new_one(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Ravi <ravi@example.com>", "Roadmap", "Can we meet at 2 PM?")]
    desk.run(asks_first)
    first_id, _, _ = the_alert(desk)
    client.post(f"/api/v1/reminders/{first_id}/answer", json={"answer": "Not sure"}, headers=auth)
    desk.mails = []
    desk.run(lambda prompt: {"tool": "ask_you", "args": {"question": "Still unsure: decline?"}})
    asks = desk.memory()["desk_asks"]
    assert len(asks) == 1 and first_id not in asks
    assert next(iter(asks.values()))["question"] == "Still unsure: decline?"


def test_each_action_is_recorded_as_a_card_and_lights_its_step(client, auth, monkeypatch):
    from sqlalchemy import select

    from mavo.database import TaskRun

    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Ravi <ravi@example.com>", "Roadmap", "Can we meet at 4 PM?")]

    def brain(prompt):
        steps = did(prompt)
        if "find_free_times" not in steps:
            return {
                "tool": "find_free_times",
                "args": {"offered": [{"start": at(16), "end": at(16, 30)}]},
            }
        if "calendar.create" not in steps:
            return {
                "tool": "calendar.create",
                "args": {"summary": "Roadmap", **found(prompt)["chosen"]},
            }
        if "gmail.send" not in steps:
            return {
                "tool": "gmail.send",
                "args": {"body": "Booked: 9:99 sharp."},
            }  # refused: not a time code gave
        return {"tool": "done", "args": {"summary": "ok"}}

    desk.run(brain)
    with desk.runtime.factory() as session:
        run = session.scalars(select(TaskRun).order_by(TaskRun.created_at.desc())).first()
        thoughts = next(s["thinking"] for s in run.steps if s.get("thinking"))
    cards = {t["tool"]: t for t in thoughts if t.get("tool")}
    assert cards["read"]["text"] == "Reading it."
    assert cards["find_free_times"]["outcome"] == "done"
    assert cards["find_free_times"]["result"].startswith("Free:")
    booked = cards["calendar.create"]
    assert booked["outcome"] == "done" and booked["about"].endswith("Roadmap")
    assert "PM" in booked["about"]  # the time in words
    sent = cards["gmail.send"]
    assert sent["outcome"] == "refused" and sent["about"] == "To Ravi: Booked: 9:99 sharp."


def test_the_app_sees_each_open_question_and_it_goes_once_answered(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Ravi <ravi@example.com>", "Roadmap", "Can we meet at 2 PM?")]
    desk.run(asks_first)
    alert_id, _, _ = the_alert(desk)

    def open_asks():
        crews = client.get("/api/v1/crew", headers=auth).json()
        return next(c for c in crews if c["id"] == desk.crew_id)["asks"]

    assert open_asks() == [
        {
            "id": alert_id,
            "question": "Their 2 PM is busy. What should I do?",
            "choices": ["Offer other times", "Ask them to pick"],
        }
    ]
    url = f"/api/v1/reminders/{alert_id}/answer"
    assert client.post(url, json={"answer": "Ask them to pick"}, headers=auth).status_code == 204
    assert open_asks() == []  # answered in the app: its alert is done too
    assert the_alert(desk)[2] == "dismissed"


def test_what_you_tell_it_in_its_room_shapes_every_run(client, auth, monkeypatch):
    """"Only reply on weekdays" told in its room reached ordinary agents but never these: they
    saw only the job they were made with."""
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    with desk.runtime.factory() as session:
        crew = session.get(CrewInstance, desk.crew_id)
        crew.configuration_plan = {
            **crew.configuration_plan,
            "preferences": [{"text": "Keep replies to two sentences.", "at": "2026-11-15"}],
        }
        session.commit()
    desk.mails = [mail(1, "Ravi <ravi@example.com>", "Roadmap", "Can we meet at 2 PM?")]
    seen = []

    def reads(prompt):
        seen.append(prompt)
        return {"tool": "done", "args": {"summary": "ok"}}

    desk.run(reads)
    job = seen[0].split("YOUR JOB (the person's words):")[1].split("SETTINGS:")[0]
    assert "Keep replies to two sentences." in job
