"""The person's Telegram bot, against a fake Telegram: never the real one, never a real token."""

import asyncio
import json
from datetime import timedelta

import httpx

from mavo.database import CrewInstance, ReminderEvent, utcnow
from mavo.telegram import plain

TOKEN = "123456789:" + "A" * 35
ME = 777


def fake_telegram(bridge):
    """Records what Mavo sent; `inbox` holds the updates Telegram would hand back next."""
    state = {"sent": [], "inbox": [], "calls": []}

    async def handler(request: httpx.Request):
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        state["calls"].append(method)
        if TOKEN not in request.url.path:
            return httpx.Response(401, json={"ok": False, "error_code": 401})
        if method == "getMe":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"id": 1, "first_name": "Mavo", "username": "my_mavo_bot"},
                },
            )
        if method == "getUpdates":
            updates, state["inbox"] = state["inbox"], []
            return httpx.Response(200, json={"ok": True, "result": updates})
        if method == "sendMessage":
            state["sent"].append(body)
            return httpx.Response(
                200, json={"ok": True, "result": {"message_id": 100 + len(state["sent"])}}
            )
        return httpx.Response(200, json={"ok": True, "result": True})

    bridge.transport = httpx.MockTransport(handler)
    return state


def message(text, chat=ME, update_id=[0], reply_to=None):  # noqa: B006 - a counter
    update_id[0] += 1
    msg = {"chat": {"id": chat}, "from": {"first_name": "Asha"}, "text": text}
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": update_id[0], "message": msg}


def linked(client, auth):
    runtime = client.app.state.crew_runtime
    bridge = runtime.telegram
    tg = fake_telegram(bridge)
    bad = client.put("/api/v1/telegram/token", headers=auth, json={"token": "not a token at all"})
    assert bad.status_code == 400 and "BotFather" in bad.json()["detail"]
    status = client.put("/api/v1/telegram/token", headers=auth, json={"token": TOKEN}).json()
    assert status["connected"] and not status["linked"]
    assert status["link_url"].startswith("https://t.me/my_mavo_bot?start=")
    code = status["link_code"]
    assert len(code) == 6 and code.isdigit()  # easy to type on a phone
    # Start pressed on the phone, without the code: told what to send, not linked.
    tg["inbox"] = [message("/start")]
    asyncio.run(bridge.tick(0))
    assert not bridge.status()["linked"]
    assert "send the 6-digit code shown in Mavo" in tg["sent"][-1]["text"]
    wrong = "000000" if code != "000000" else "111111"
    tg["inbox"] = [message(wrong)]
    asyncio.run(bridge.tick(0))
    assert not bridge.status()["linked"] and "not right" in tg["sent"][-1]["text"]
    # Typed with a space, as people do: linked.
    tg["inbox"] = [message(code[:3] + " " + code[3:])]
    asyncio.run(bridge.tick(0))
    assert bridge.status()["linked"] and bridge.status()["chat_name"] == "Asha"
    assert "Linked." in tg["sent"][-1]["text"]
    assert bridge.status()["link_code"] is None  # used up
    assert TOKEN not in json.dumps(bridge.status())  # the token is never shown
    return runtime, bridge, tg


def add_crew(runtime, name="Momentum Spark"):
    with runtime.factory() as session:
        crew = CrewInstance(
            name=name,
            template_id="research",
            responsibilities="Motivate me.",
            provider="ollama",
            model="m",
            status="active",
            permissions=["model:invoke", "notifications:show"],
            configuration_plan={},
            trigger_type="interval",
            is_running=True,
        )
        session.add(crew)
        session.commit()
        return crew.id


def test_linking_needs_the_one_time_code_and_strangers_are_ignored(client, auth):
    runtime, bridge, tg = linked(client, auth)
    before = len(tg["sent"])
    tg["inbox"] = [message("@Momentum Spark hi", chat=12345)]
    asyncio.run(bridge.tick(0))
    assert len(tg["sent"]) == before  # nobody else gets a reply


def test_a_tagged_message_talks_to_that_agent_and_a_late_answer_follows(client, auth):
    runtime, bridge, tg = linked(client, auth)
    crew_id = add_crew(runtime)
    add_crew(runtime, "Signal Inbox Brief")
    asked = []
    answers = {"q1": {"id": "q1", "answer": "", "pending": True}}

    async def ask(crew, payload):
        asked.append((crew, payload.question))
        return {"id": "q1", "kind": "question", "answer": "", "pending": True}

    runtime.ask_crew = ask
    runtime.ask_items = lambda crew: [answers["q1"]]
    tg["inbox"] = [message("@momentumspark give me a quote")]
    asyncio.run(bridge.tick(0))
    assert asked == [(crew_id, "give me a quote")]
    assert "Working on it" in tg["sent"][-1]["text"]
    answers["q1"] = {"id": "q1", "answer": r"\textbf{Keep going.}", "pending": False}
    asyncio.run(bridge.tick(0))
    assert tg["sent"][-1]["text"] == "<b>Momentum Spark</b>\n<b>Keep going.</b>"
    # Untagged, it asks which agent and lists them.
    tg["inbox"] = [message("give me a quote")]
    asyncio.run(bridge.tick(0))
    assert (
        "Which agent" in tg["sent"][-1]["text"] and "@Signal Inbox Brief" in tg["sent"][-1]["text"]
    )


def test_alerts_go_to_telegram_once_when_chosen_and_questions_answer_from_there(client, auth):
    runtime, bridge, tg = linked(client, auth)
    crew_id = add_crew(runtime)
    now = utcnow()
    with runtime.factory() as session:
        session.add(
            ReminderEvent(
                crew_id=crew_id,
                crew_name="Momentum Spark",
                kind="crew_update",
                message=r"\textbf{Small steps.}",
                scheduled_for=now,
                local_date=now.date().isoformat(),
                status="pending",
                deduplication_key="a1",
            )
        )
        crew = session.get(CrewInstance, crew_id)
        question = ReminderEvent(
            crew_id=crew_id,
            crew_name="Momentum Spark",
            kind="question",
            message="Which theme today?",
            scheduled_for=now,
            local_date=now.date().isoformat(),
            status="pending",
            deduplication_key="q1",
        )
        session.add(question)
        session.flush()
        crew.observation = {"desk_asks": {question.id: {"choices": ["Focus", "Rest"]}}}
        session.add(
            ReminderEvent(
                crew_id=crew_id,
                crew_name="Later",
                kind="crew_update",
                message="Not yet",
                scheduled_for=now + timedelta(hours=1),
                local_date=now.date().isoformat(),
                status="pending",
                deduplication_key="later",
            )
        )
        session.commit()
        question_id = question.id
    asyncio.run(bridge.tick(0))
    asyncio.run(bridge.tick(0))  # twice: still sent once
    alerts = [m for m in tg["sent"] if "Momentum Spark" in m["text"]]
    assert [m["text"].split("\n")[1] for m in alerts] == [
        "<b>Small steps.</b>",
        "Which theme today?",
    ]
    buttons = alerts[1]["reply_markup"]["inline_keyboard"]
    assert [b[0]["text"] for b in buttons] == ["Focus", "Rest"]
    assert not [m for m in tg["sent"] if "Not yet" in m["text"]]  # not due yet
    # Pressing a choice answers it, as the desktop alert would.
    tg["inbox"] = [
        {
            "update_id": 999,
            "callback_query": {
                "id": "cb",
                "data": buttons[1][0]["callback_data"],
                "message": {"chat": {"id": ME}},
            },
        }
    ]
    asyncio.run(bridge.tick(0))
    with runtime.factory() as session:
        asks = session.get(CrewInstance, crew_id).observation["desk_asks"]
        assert asks[question_id]["answer"] == "Rest"
    # Turned off, nothing more goes.
    client.put("/api/v1/telegram/alerts", headers=auth, json={"on": False})
    with runtime.factory() as session:
        session.add(
            ReminderEvent(
                crew_id=crew_id,
                crew_name="Momentum Spark",
                kind="crew_update",
                message="Quiet now",
                scheduled_for=utcnow(),
                local_date=now.date().isoformat(),
                status="pending",
                deduplication_key="a2",
            )
        )
        session.commit()
    asyncio.run(bridge.tick(0))
    assert not [m for m in tg["sent"] if "Quiet now" in m["text"]]


def test_disconnecting_forgets_the_token_and_the_chat(client, auth):
    runtime, bridge, tg = linked(client, auth)
    assert client.delete("/api/v1/telegram", headers=auth).status_code == 204
    assert bridge.credentials.get("telegram-bot") is None
    assert client.get("/api/v1/telegram", headers=auth).json()["connected"] is False


def test_reports_read_cleanly_in_telegram():
    text = (
        r"\section{Inbox}"
        "\n"
        r"\begin{itemize}"
        "\n"
        r"\item \textbf{From:} Ravi <ravi@x>"
        "\n"
        r"\end{itemize}"
        "\n"
        r"Spent \$5 \& more."
    )
    assert plain(text) == "<b>Inbox</b>\n\n• <b>From:</b> Ravi &lt;ravi@x&gt;\nSpent $5 &amp; more."


def test_an_old_code_or_many_guesses_link_nothing(client, auth):
    runtime = client.app.state.crew_runtime
    bridge = runtime.telegram
    tg = fake_telegram(bridge)
    code = client.put("/api/v1/telegram/token", headers=auth, json={"token": TOKEN}).json()[
        "link_code"
    ]
    with bridge.factory() as session:  # 31 minutes later
        value = bridge.state(session)
        value["code_until"] = (utcnow() - timedelta(minutes=1)).isoformat()
        bridge.save(session, value)
    assert client.get("/api/v1/telegram", headers=auth).json()["code_expired"] is True
    tg["inbox"] = [message(code)]
    asyncio.run(bridge.tick(0))
    assert not bridge.status()["linked"] and "expired" in tg["sent"][-1]["text"]
    code = client.post("/api/v1/telegram/code", headers=auth).json()["link_code"]
    wrong = "000000" if code != "000000" else "111111"
    tg["inbox"] = [message(wrong, chat=55) for _ in range(5)] + [message(code, chat=55)]
    asyncio.run(bridge.tick(0))
    assert not bridge.status()["linked"]  # five wrong guesses, and that chat is ignored
    tg["inbox"] = [message(code)]
    asyncio.run(bridge.tick(0))
    assert bridge.status()["linked"]  # the person's own chat still links


def test_an_agent_asked_to_send_on_telegram_goes_there_and_the_planner_knows_telegram(client, auth):
    """'Send on my telegram too' was answered "Telegram delivery isn't supported": the planner
    was never told the person's bot was linked."""
    from mavo.coordinator_contract import PlannerContext, coordinator_prompt
    from mavo.resource_pool import brief

    runtime, bridge, tg = linked(client, auth)
    with runtime.factory() as session:
        pool = brief(session, runtime)
    assert pool[0] == {
        "id": "telegram",
        "name": "Telegram",
        "connected": True,
        "level": "all alerts",
        "readable": False,
        "actions": [],
    }
    prompt = coordinator_prompt(PlannerContext(local_time="2026-11-16T09:00:00+05:30"))
    assert "requested_telegram true" in prompt and "Never say Telegram is not supported" in prompt
    client.put("/api/v1/telegram/alerts", headers=auth, json={"on": False})
    chosen, other = add_crew(runtime, "Inbox Rhythm"), add_crew(runtime, "Quiet One")
    now = utcnow()
    with runtime.factory() as session:
        session.get(CrewInstance, chosen).configuration_plan = {
            "draft": {"requested_telegram": True}
        }
        for crew_id, text in ((chosen, "Digest ready"), (other, "Not for Telegram")):
            session.add(
                ReminderEvent(
                    crew_id=crew_id,
                    crew_name="x",
                    kind="crew_update",
                    message=text,
                    scheduled_for=now,
                    local_date=now.date().isoformat(),
                    status="pending",
                    deduplication_key=text,
                )
            )
        session.commit()
    asyncio.run(bridge.tick(0))
    texts = " ".join(m["text"] for m in tg["sent"])
    assert "Digest ready" in texts and "Not for Telegram" not in texts


def test_the_telegram_switch_on_an_agent_is_saved(client, auth):
    runtime = client.app.state.crew_runtime
    crew_id = add_crew(runtime, "Inbox Rhythm")
    with runtime.factory() as session:
        session.get(CrewInstance, crew_id).configuration_plan = {"draft": {"name": "x"}}
        session.commit()
    on = client.post(f"/api/v1/crew/{crew_id}/telegram", headers=auth, json={"on": True})
    assert on.status_code == 200
    assert on.json()["configuration_plan"]["draft"]["requested_telegram"] is True
    off = client.post(f"/api/v1/crew/{crew_id}/telegram", headers=auth, json={"on": False}).json()
    assert off["configuration_plan"]["draft"]["requested_telegram"] is False
    assert (
        client.post("/api/v1/crew/nope/telegram", headers=auth, json={"on": True}).status_code
        == 404
    )


def test_remind_you_goes_where_the_person_chose(client, auth):
    """One action, user.remind: desktop and Telegram when both are on, only the one that is."""
    from mavo.crew_workers import reaches_person

    runtime, bridge, tg = linked(client, auth)
    workers = runtime  # the worker runtime is the crew runtime
    both = add_crew(runtime, "Both")
    with runtime.factory() as session:
        crew = session.get(CrewInstance, both)
        assert reaches_person(workers, crew)
        assert workers.remind_user(crew, "Stand up and stretch.") == ["your desktop", "Telegram"]
    asyncio.run(bridge.tick(0))
    assert any("Stand up and stretch." in m["text"] for m in tg["sent"])
    with runtime.factory() as session:
        event = session.query(ReminderEvent).filter(ReminderEvent.crew_id == both).one()
        assert event.status == "pending"  # the desktop shows it too
    # Telegram only: the desktop never sees it, Telegram does.
    client.put("/api/v1/telegram/alerts", headers=auth, json={"on": False})
    only_tg = add_crew(runtime, "Phone only")
    with runtime.factory() as session:
        crew = session.get(CrewInstance, only_tg)
        crew.permissions = ["model:invoke"]
        crew.configuration_plan = {"draft": {"requested_telegram": True}}
        session.commit()
        assert workers.remind_user(crew, "Drink water.") == ["Telegram"]
        assert (
            session.query(ReminderEvent).filter(ReminderEvent.crew_id == only_tg).one().status
            == "telegram"
        )
    asyncio.run(bridge.tick(0))
    assert any("Drink water." in m["text"] for m in tg["sent"])
    # Neither on: it cannot remind, and is not offered the action.
    neither = add_crew(runtime, "Silent")
    with runtime.factory() as session:
        crew = session.get(CrewInstance, neither)
        crew.permissions = ["model:invoke"]
        session.commit()
        assert not reaches_person(workers, crew)
        assert workers.remind_user(crew, "x") == []
