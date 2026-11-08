# ruff: noqa: F811, E501
"""Lines of agents: a maker, a reviewer that sends work back until it is right, and a publisher
that waits for the person's OK. The work, and the files it made, go along the line."""

from mavo.database import ActionRequest, CrewInstance
from mavo.pipelines import decide, parse_verdict, route_finished
from tests.test_multi_agent import batch, crew, make_session
from tests.test_planner import planning, turn  # noqa: F401


def agent(id_, name, **plan):
    upstream = plan.pop("upstream", [])
    return CrewInstance(
        id=id_,
        name=name,
        template_id="operator",
        status="active",
        responsibilities=name,
        provider="ollama",
        model="m",
        is_running=True,
        runtime_state="waiting_event",
        trigger_type="manual" if upstream else "interval",
        configuration_plan={"upstream": upstream, **plan},
    )


def line(tmp_path, **publisher):
    factory = make_session(tmp_path)
    with factory() as session:
        session.add_all(
            [
                agent("maker", "Video Script Writer"),
                agent("review", "Script Reviewer", upstream=["maker"], review=True, max_rounds=2),
                agent("publish", "YouTube Uploader", upstream=["review"], **publisher),
            ]
        )
        session.commit()
    return factory


def test_a_verdict_is_read_from_its_own_line_only():
    assert parse_verdict("Too long.\nCut the intro.\nVERDICT: fix") == (
        "fix",
        "Too long.\nCut the intro.",
    )
    assert parse_verdict("Great.\nverdict: PASS") == ("pass", "Great.")
    assert parse_verdict("The VERDICT: fix is not on its own line") == (
        None,
        "The VERDICT: fix is not on its own line",
    )


def test_the_made_work_and_its_files_go_to_the_reviewer(tmp_path):
    factory = line(tmp_path)
    with factory() as session:
        session.add(
            ActionRequest(
                crew_id="maker",
                crew_name="Video Script Writer",
                source="crew",
                operation="notes.write",
                arguments={},
                status="completed",
                result={"created": True, "path": "~/Videos/day-12/script.md"},
                account_subject="local",
                policy_revision="r",
                target_summary="",
                fingerprint="f1",
            )
        )
        session.commit()
        maker = session.get(CrewInstance, "maker")
        route_finished(session, maker, "Script for day 12: three tips for faster laptops.")
        session.commit()
        review = session.get(CrewInstance, "review")
        handed = review.observation["handoffs"][-1]
        assert handed["from"] == "Video Script Writer" and handed["files"] == [
            "~/Videos/day-12/script.md"
        ]
        assert review.runtime_state == "waiting"  # woken to review it


def test_fix_sends_it_back_to_the_maker_until_the_limit_then_the_person_decides(tmp_path):
    factory = line(tmp_path)
    with factory() as session:
        maker, review = session.get(CrewInstance, "maker"), session.get(CrewInstance, "review")
        route_finished(session, maker, "Draft 1")
        for round_ in (1, 2):
            route_finished(session, review, f"The hook is weak (round {round_}).\nVERDICT: fix")
            session.commit()
            back = maker.observation["handoffs"][-1]
            assert (
                back["kind"] == "revision"
                and back["round"] == round_
                and "hook is weak" in back["result"]
            )
            assert maker.runtime_state == "waiting"  # it does it again
            route_finished(session, maker, f"Draft {round_ + 1}")
        route_finished(
            session, review, "Still weak.\nVERDICT: fix"
        )  # a third time: over the limit of 2
        session.commit()
        assert (
            review.runtime_state == "needs_input"
            and "sent the work back 2 times" in review.question
        )
        assert review.held["kind"] == "review_stuck" and review.held["result"] == "Draft 3"
        decide(session, review, approve=True, note="Good enough for today")
        session.commit()
        publish = session.get(CrewInstance, "publish")
        assert (
            "Draft 3" in publish.observation["handoffs"][-1]["result"]
            and "Good enough for today" in publish.observation["handoffs"][-1]["result"]
        )
        assert review.held is None and review.observation["review_round"] == 0


def test_pass_sends_the_checked_work_on_with_the_reviewers_notes(tmp_path):
    factory = line(tmp_path)
    with factory() as session:
        maker, review = session.get(CrewInstance, "maker"), session.get(CrewInstance, "review")
        route_finished(session, maker, "Final script")
        route_finished(session, review, "Tight and clear.\nVERDICT: pass")
        session.commit()
        work = session.get(CrewInstance, "publish").observation["handoffs"][-1]
        assert (
            work["from"] == "Script Reviewer"
            and work["result"].startswith("Final script")
            and "Reviewed by Script Reviewer: Tight and clear." in work["result"]
        )


def test_a_publisher_that_asks_first_holds_the_work_until_the_person_says(tmp_path):
    factory = line(tmp_path, approve_first=True)
    with factory() as session:
        maker, review, publish = (
            session.get(CrewInstance, i) for i in ("maker", "review", "publish")
        )
        route_finished(session, maker, "Final script")
        route_finished(session, review, "VERDICT: pass")
        session.commit()
        assert (
            publish.runtime_state == "needs_input"
            and "Script Reviewer handed over work" in publish.question
        )
        assert not publish.observation.get("handoffs")  # nothing to act on until approved
        decide(session, publish, approve=False, note="Use a brighter thumbnail")
        session.commit()
        assert publish.held is None and publish.runtime_state == "waiting_event"
        assert (
            review.observation["handoffs"][-1]["kind"] == "revision"
            and "brighter thumbnail" in review.observation["handoffs"][-1]["result"]
        )
        route_finished(session, review, "VERDICT: pass")
        decide(session, publish, approve=True)
        session.commit()
        assert publish.runtime_state == "waiting" and publish.observation["handoffs"][-1][
            "result"
        ].startswith("Final script")


def test_the_line_is_set_and_changed_from_the_app_and_never_loops(planning):
    client, auth, runtime, coordinator = planning
    coordinator.decision = batch(
        crew("Video Script Writer", trigger_type="interval"),
        crew(
            "Script Reviewer",
            trigger_type="manual",
            depends_on=["Video Script Writer"],
            review=True,
            max_rounds=2,
        ),
        crew(
            "YouTube Uploader",
            trigger_type="manual",
            depends_on=["Script Reviewer"],
            approve_first=True,
        ),
    )
    made = {c["name"]: c for c in turn(planning, "Post a video on YouTube every day")["crews"]}
    plan = made["Script Reviewer"]["configuration_plan"]
    assert plan["review"] is True and plan["max_rounds"] == 2
    assert made["YouTube Uploader"]["configuration_plan"]["approve_first"] is True
    with runtime.factory() as session:  # activate them, as Verify & add does
        for c in made.values():
            row = session.get(CrewInstance, c["id"])
            row.status = "active"
        session.commit()
    writer, reviewer = made["Video Script Writer"]["id"], made["Script Reviewer"]["id"]
    loop = client.patch(f"/api/v1/crew/{writer}", headers=auth, json={"upstream": [reviewer]})
    assert loop.status_code == 409 and "wait on each other" in loop.json()["detail"]
    changed = client.patch(
        f"/api/v1/crew/{reviewer}", headers=auth, json={"max_rounds": 4, "approve_first": True}
    ).json()
    assert (
        changed["configuration_plan"]["max_rounds"] == 4
        and changed["configuration_plan"]["approve_first"] is True
    )
    nothing = client.post(f"/api/v1/crew/{reviewer}/handover", headers=auth, json={"approve": True})
    assert nothing.status_code == 409


# ── Teams: the lead decides each step ─────────────────────────────────────────────────────────
import json  # noqa: E402

from mavo.pipelines import STEP_LIMIT  # noqa: E402


def team(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        lead = agent("lead", "Channel Lead", leads_team=True, members=["scout", "writer"])
        lead.trigger_type = "interval"
        session.add_all(
            [
                lead,
                agent("scout", "Topic Scout", lead="lead"),
                agent("writer", "Script Writer", lead="lead"),
            ]
        )
        session.commit()
    return factory


def decision(**d):
    return "Thinking it over.\nDECISION: " + json.dumps(d)


def test_the_lead_gives_a_member_a_task_and_hears_back_with_its_files(tmp_path):
    factory = team(tmp_path)
    with factory() as session:
        lead, writer = session.get(CrewInstance, "lead"), session.get(CrewInstance, "writer")
        route_finished(
            session, lead, decision(next="script writer", task="Write a 60-second script on tip 3")
        )
        session.commit()
        task = writer.observation["handoffs"][-1]
        assert (
            task["kind"] == "task" and task["from"] == "Channel Lead" and "tip 3" in task["result"]
        )
        assert writer.runtime_state == "waiting" and lead.observation["round_steps"] == 1
        session.add(
            ActionRequest(
                crew_id="writer",
                crew_name="Script Writer",
                source="crew",
                operation="notes.write",
                arguments={},
                status="completed",
                result={"path": "~/Videos/tip3.md"},
                account_subject="local",
                policy_revision="r",
                target_summary="",
                fingerprint="f2",
            )
        )
        lead.runtime_state = "waiting_event"
        route_finished(session, writer, "Script written.")
        session.commit()
        report = lead.observation["handoffs"][-1]
        assert (
            report["kind"] == "report"
            and report["files"] == ["~/Videos/tip3.md"]
            and lead.runtime_state == "waiting"
        )
        assert not session.get(CrewInstance, "scout").observation.get(
            "handoffs"
        )  # members only hear from the lead


def test_the_lead_asks_the_person_and_hears_the_answer(tmp_path):
    factory = team(tmp_path)
    with factory() as session:
        lead = session.get(CrewInstance, "lead")
        route_finished(session, lead, decision(ask="Publish today's video as public?"))
        session.commit()
        assert (
            lead.runtime_state == "needs_input"
            and lead.question == "Publish today's video as public?"
            and lead.held["kind"] == "ask"
        )
        decide(session, lead, approve=False, note="Keep it unlisted")
        session.commit()
        answer = lead.observation["handoffs"][-1]
        assert (
            answer["kind"] == "person"
            and answer["result"] == "No. Keep it unlisted"
            and lead.runtime_state == "waiting"
            and lead.held is None
        )


def test_a_round_that_runs_on_is_stopped_and_a_bad_member_name_is_asked_about(tmp_path):
    factory = team(tmp_path)
    with factory() as session:
        lead = session.get(CrewInstance, "lead")
        lead.observation = {"round_steps": STEP_LIMIT}
        route_finished(session, lead, decision(next="Topic Scout", task="Again"))
        session.commit()
        assert lead.held and f"{STEP_LIMIT} steps" in lead.question
        decide(session, lead, approve=True)
        route_finished(session, lead, decision(next="Video Editor", task="Edit"))
        session.commit()
        assert "does not exist" in lead.question


def test_done_ends_the_round_and_hands_the_result_on(tmp_path):
    factory = team(tmp_path)
    with factory() as session:
        lead = session.get(CrewInstance, "lead")
        session.add(agent("after", "Weekly Digest", upstream=["lead"]))
        lead.observation = {"round_steps": 4}
        route_finished(session, lead, decision(done="Day 12 video scripted and reviewed"))
        session.commit()
        assert lead.observation["round_steps"] == 0
        assert (
            session.get(CrewInstance, "after").observation["handoffs"][-1]["result"]
            == "Day 12 video scripted and reviewed"
        )


def test_the_planner_builds_a_team_the_model_designed(planning):
    client, auth, runtime, coordinator = planning
    coordinator.decision = batch(
        crew("Channel Lead", trigger_type="interval", leads_team=True),
        crew("Trend Researcher", lead="Channel Lead"),
        crew("Script Writer", lead="Channel Lead"),
        crew("Thumbnail Designer", lead="Channel Lead"),
    )
    made = {
        c["name"]: c for c in turn(planning, "Run my YouTube channel: a video every day")["crews"]
    }
    lead = made["Channel Lead"]
    assert lead["configuration_plan"]["leads_team"] is True
    assert sorted(lead["configuration_plan"]["members"]) == sorted(
        made[n]["id"] for n in ("Trend Researcher", "Script Writer", "Thumbnail Designer")
    )
    assert (
        made["Script Writer"]["configuration_plan"]["lead"] == lead["id"]
        and made["Script Writer"]["trigger_type"] == "manual"
    )
    coordinator.decision = batch(crew("Lonely", lead="Nobody"))
    bad = client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={"message": "x", "request_id": "22222222-2222-2222-2222-222222222222"},
    )
    assert bad.status_code >= 400
