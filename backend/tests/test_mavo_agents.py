# ruff: noqa: E501, F811
"""Mavo's own agents: "@web ..." in the chat goes straight to a fixed one-time task."""

import asyncio
import json

import pytest

from mavo.mavo_agents import AGENTS, decision_for
from mavo.site_skills import (
    GatherParams,
    WebParams,
    gather,
    page_text,
    public_https,
    readable,
    web_read,
)
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_tasks import allow_browser, scripted, wait


def test_a_message_to_an_agent_becomes_its_fixed_task_and_anything_else_is_left_alone():
    web = decision_for("@web best budget laptops in India")
    assert web.reply == "Web is on it." and web.task is not None
    kinds = [(s.kind, s.skill) for s in web.task.steps]
    assert kinds == [("skill", "web.read"), ("helper", None)]
    assert web.task.final == "answer" and web.task.steps[1].uses == ["fetch"]
    assert web.task.steps[0].params == {"query": "best budget laptops in India", "pages": 3}
    news = decision_for("@news, Agniveer scheme")
    assert news.task.steps[0].params == {
        "query": "Agniveer scheme",
        "sources": ["news", "wikipedia"],
    }
    places = decision_for("@places drive time to Chennai airport")
    step = places.task.steps[0]
    assert step.kind == "agent" and step.open_data and step.actions == []
    assert decision_for("@search cats").task is not None  # an alias of web
    # Not addressed to an agent: the assistant plans it as usual.
    for other in ("web cats", "email @web please", "@nobody hi", ""):
        assert decision_for(other) is None
    hint = decision_for("@web")
    assert hint.task is None and "@web best budget" in hint.reply
    off = decision_for("@news AI", open_data_on=False)
    assert off.task is None and "Open data" in off.reply
    assert (
        decision_for("@web AI", open_data_on=False).task is not None
    )  # web needs only the browser


def test_every_agent_is_described_and_makes_a_valid_plan():
    for agent in AGENTS.values():
        assert agent.name and agent.tagline and agent.example.startswith("@" + agent.id)
        plan = agent.plan("something to find")
        assert plan.steps and plan.final in {s.id for s in plan.steps}


def test_the_chat_sends_an_agent_message_to_the_agent_without_a_planning_call(planning):
    client, auth, runtime, coordinator = planning
    client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "m"}
    ).raise_for_status()
    scripted(runtime, helper={"writer": "Agnipath: four years, then a merit-based chance. [1]"})

    async def fetched(desktop, rule, owner, params):
        return json.dumps(
            [
                {
                    "source": "Google News",
                    "status": "ok",
                    "result": {"headlines": [{"title": "Agniveer changes"}]},
                }
            ]
        )

    from mavo.site_skills import SKILLS

    real = SKILLS["open_data.gather"]
    SKILLS["open_data.gather"] = type(real)(**{**real.__dict__, "run": fetched})
    try:
        calls = len(coordinator.calls)
        answer = turn(planning, "@news Agniveer scheme")
        assert len(coordinator.calls) == calls  # no planning call
        assert answer["reply"] == "News is on it." and answer["task_run_id"]
        done = wait(client, auth, answer["task_run_id"])
    finally:
        SKILLS["open_data.gather"] = real
    assert done["status"] == "done", done
    assert done["result"].startswith("Agnipath: four years")
    assert [s["kind"] for s in done["steps"]] == ["skill", "helper"]
    assert client.get("/api/v1/mavo-agents", headers=auth).json()[0]["id"] == "web"


# ---- the web, read by Mavo ----------------------------------------------------------------------


class FakeDesktop:
    def __init__(self, results, pages):
        self.results, self.pages, self.opened = results, pages, []
        self.page = self

    def check(self):
        pass

    async def browser(self, operation, args, rule, owner, scope_guard=None, limit=None):
        if operation == "browser.search":
            return {"results": self.results}
        self.opened.append(args["url"])
        self.current = args["url"]
        return {"url": args["url"]}

    def locator(self, _):
        return self

    async def inner_text(self, timeout=None):
        return self.pages.get(self.current, "")


SENTENCE = (
    "This laptop has a fast processor, a bright screen and a battery that lasts all day long. "
)


def test_the_web_agent_reads_pages_quickly_and_uses_the_browser_only_for_the_rest(monkeypatch):
    results = [
        {"title": f"R{n}", "url": f"https://site{n}.example/a", "snippet": "s"} for n in range(6)
    ]

    async def quick(urls):  # sites 0 and 2 answer a plain request; the others refuse it
        return {u: SENTENCE * 8 for u in urls if u.startswith(("https://site0", "https://site2"))}

    monkeypatch.setattr("mavo.site_skills.fetch_pages", quick)
    desktop = FakeDesktop(results, {"https://site1.example/a": "Menu\nHome\n" + SENTENCE * 6})
    rule = {"mode": "unrestricted", "domains": []}
    out = json.loads(asyncio.run(web_read(desktop, rule, "o", WebParams(query="budget laptop"))))
    assert [p["url"] for p in out["pages_read"]] == [
        "https://site0.example/a",
        "https://site2.example/a",
        "https://site1.example/a",
    ]
    assert desktop.opened == ["https://site1.example/a"]  # only the one still missing
    assert "Menu" not in out["pages_read"][2]["text"]  # menus are not prose
    assert {r["url"] for r in out["other_results"]}.isdisjoint(
        {p["url"] for p in out["pages_read"]}
    )


def test_the_web_agent_keeps_to_the_sites_browser_is_limited_to(monkeypatch):
    seen = []

    async def quick(urls):
        seen.extend(urls)
        return {u: SENTENCE * 8 for u in urls}

    monkeypatch.setattr("mavo.site_skills.fetch_pages", quick)
    results = [
        {"title": "ok", "url": "https://allowed.example/x"},
        {"title": "no", "url": "https://other.example/y"},
    ]
    rule = {"mode": "restricted", "domains": ["allowed.example"]}
    out = json.loads(
        asyncio.run(web_read(FakeDesktop(results, {}), rule, "o", WebParams(query="laptops")))
    )
    assert seen == ["https://allowed.example/x"] and len(out["pages_read"]) == 1


def test_pages_are_read_as_prose_and_only_public_https_addresses_are_fetched():
    html = (
        "<html><nav>Home About</nav><script>x()</script><p>"
        + SENTENCE
        + "</p><footer>c</footer></html>"
    )
    assert readable(page_text(html)).strip() == SENTENCE.strip()
    for private in (
        "http://example.com",
        "https://localhost/a",
        "https://127.0.0.1/a",
        "https://user:pw@example.com",
    ):
        assert not asyncio.run(public_https(private)), private


def test_gather_reports_nothing_came_back_rather_than_an_empty_answer(monkeypatch):
    from mavo.site_skills import SkillStop

    async def nothing(request, fetch=None):
        return {"source": request.source, "status": "unavailable", "result": {"error": "down"}}

    monkeypatch.setattr("mavo.resources.open_data.look_up", nothing)
    with pytest.raises(SkillStop, match="Nothing came back: down"):
        asyncio.run(gather(None, {}, "o", GatherParams(query="x", sources=["news"])))
    with pytest.raises(ValueError):
        GatherParams(query="x", sources=["http://evil"])


def test_a_web_task_needs_the_browser_actions_turned_on(client, auth):
    from tests.test_tasks import setup, start

    setup(client, auth)
    plan = decision_for("@web cats").task.model_dump(mode="json")
    blocked = wait(client, auth, start(client, auth, plan))
    assert blocked["status"] == "needs_you" and "Turn on in Actions" in blocked["error"]
    allow_browser(client, auth)  # search still has to be on
    again = wait(client, auth, start(client, auth, plan))
    assert again["status"] == "needs_you" and "Search the web" in again["error"]
