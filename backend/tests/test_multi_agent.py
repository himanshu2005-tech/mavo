# ruff: noqa: F811, E501
"""Daily trigger instances, Crew batches and the dependency graph, the resource pool ceiling,
and the timeline."""

import json
from datetime import UTC, datetime, timedelta, timezone

from mavo.daily_triggers import DailyTrigger, next_occurrence
from mavo.database import CrewInstance, ReminderEvent
from mavo.migrations import run_migrations
from mavo.reminders import create_due_reminders
from mavo.schemas import CoordinatorDecision
from mavo.timeline import crew_graph, upcoming_events
from tests.test_planner import planning, turn  # noqa: F401,F811

IST = timezone(timedelta(hours=5, minutes=30))


def meal_crew(**extra):
    return CrewInstance(
        id="meals",
        name="Meals",
        template_id="schedule",
        status="active",
        responsibilities="Meal reminders",
        provider="ollama",
        model="m",
        is_running=True,
        trigger_type="daily",
        daily_triggers=[
            DailyTrigger(time="09:00", message="Breakfast").model_dump(),
            DailyTrigger(time="13:00", message="Lunch").model_dump(),
            DailyTrigger(time="19:45", message="Dinner", weekdays=[0, 1, 2, 3, 4]).model_dump(),
        ],
        **extra,
    )


def make_session(tmp_path):
    from mavo.database import build_session_factory

    url = f"sqlite:///{(tmp_path / 'multi.db').as_posix()}"
    run_migrations(url)
    return build_session_factory(url)


def test_one_crew_delivers_each_daily_trigger_once_with_its_own_message(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        session.add(meal_crew())
        session.commit()
        # Monday 2026-11-02
        morning = create_due_reminders(session, datetime(2026, 11, 2, 9, 5, tzinfo=IST))
        assert [r.message for r in morning] == ["Breakfast"]
        noon = create_due_reminders(session, datetime(2026, 11, 2, 13, 1, tzinfo=IST))
        assert sorted(r.message for r in noon) == ["Breakfast", "Lunch"]
        again = create_due_reminders(session, datetime(2026, 11, 2, 13, 2, tzinfo=IST))
        assert len(again) == 2
        assert session.query(ReminderEvent).count() == 2
        night = create_due_reminders(session, datetime(2026, 11, 2, 19, 50, tzinfo=IST))
        assert "Dinner" in [r.message for r in night]
    factory.kw["bind"].dispose()


def test_weekday_limited_trigger_does_not_fire_on_weekend(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        session.add(meal_crew())
        session.commit()
        saturday = create_due_reminders(session, datetime(2026, 11, 7, 20, 0, tzinfo=IST))
        assert "Dinner" not in [r.message for r in saturday]
        assert {r.message for r in saturday} == {"Breakfast", "Lunch"}
    factory.kw["bind"].dispose()


def test_legacy_single_time_crew_keeps_its_old_dedup_key(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        session.add(
            CrewInstance(
                id="old",
                name="Old",
                template_id="schedule",
                status="active",
                responsibilities="Wear watch",
                provider="ollama",
                model="m",
                schedule_time="22:00",
                is_running=True,
                trigger_type="daily",
            )
        )
        session.commit()
        create_due_reminders(session, datetime(2026, 11, 1, 22, 1, tzinfo=IST))
        assert session.query(ReminderEvent).one().deduplication_key == "old:2026-11-01"
    factory.kw["bind"].dispose()


def test_next_occurrence_picks_the_earliest_enabled_trigger():
    crew = meal_crew()
    now = datetime(2026, 11, 2, 12, 0, tzinfo=IST)
    assert next_occurrence(crew, now).astimezone(IST).hour == 13
    crew.daily_triggers = [{**crew.daily_triggers[1], "enabled": False}]
    assert next_occurrence(crew, now) is None


def test_duplicate_and_bad_triggers_are_rejected():
    import pytest
    from pydantic import ValidationError

    from mavo.schemas import CrewDraft

    base = {"template_id": "schedule", "name": "Meals", "responsibilities": "Eat"}
    with pytest.raises(ValidationError):
        CrewDraft(**base, daily_triggers=[{"time": "09:00"}, {"time": "09:00"}])
    with pytest.raises(ValidationError):
        CrewDraft(**base, daily_triggers=[{"time": "25:00"}])
    with pytest.raises(ValidationError):
        CrewDraft(**base, daily_triggers=[{"time": "09:00", "weekdays": [7]}])
    draft = CrewDraft(**base, daily_triggers=[{"time": "09:00"}, {"time": "13:00"}])
    assert draft.trigger_type == "daily" and draft.schedule_time == "09:00"


def batch(*crews, **extra):
    return CoordinatorDecision.model_validate(
        {"reply": "Set up.", "crew_proposals": list(crews), **extra}
    )


def crew(name, **extra):
    return {"template_id": "schedule", "name": name, "responsibilities": name, **extra}


def test_batch_creates_linked_drafts_and_binds_them_to_the_chat(planning):
    client, auth, runtime, coordinator = planning
    coordinator.decision = batch(
        crew("Trip watch", trigger_type="interval"),
        crew("Leave for airport", trigger_type="interval", depends_on=["Trip watch"]),
    )
    result = turn(planning, "I fly to Chennai on Friday")
    assert [c["name"] for c in result["crews"]] == ["Trip watch", "Leave for airport"]
    graph = client.get("/api/v1/crew-graph", headers=auth).json()
    ids = {n["name"]: n["id"] for n in graph["nodes"]}
    assert graph["edges"] == [{"from": ids["Trip watch"], "to": ids["Leave for airport"]}]
    assert len(client.get("/api/v1/crew-drafts", headers=auth).json()) == 2


def test_batch_rejects_loops_unknown_dependencies_and_questions(planning):
    import pytest
    from pydantic import ValidationError

    client, auth, runtime, coordinator = planning
    coordinator.decision = batch(
        crew("A", trigger_type="interval", depends_on=["B"]),
        crew("B", trigger_type="interval", depends_on=["A"]),
    )
    response = client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={"message": "loop", "request_id": "11111111-1111-1111-1111-111111111111"},
    )
    assert response.status_code >= 400
    assert "loop" in response.json()["detail"]
    assert client.get("/api/v1/crew-drafts", headers=auth).json() == []
    with pytest.raises(ValidationError):
        batch(crew("A"), questions=["Which?"])


def test_finished_work_is_handed_to_the_downstream_crew(tmp_path):
    from mavo.crew_workers import deliver_handoffs

    factory = make_session(tmp_path)
    with factory() as session:
        up = CrewInstance(
            id="up",
            name="Up",
            template_id="travel",
            status="active",
            responsibilities="r",
            provider="ollama",
            model="m",
        )
        down = CrewInstance(
            id="down",
            name="Down",
            template_id="travel",
            status="active",
            responsibilities="r",
            provider="ollama",
            model="m",
            is_running=True,
            runtime_state="waiting_event",
            configuration_plan={"upstream": ["up"]},
        )
        session.add_all([up, down])
        session.commit()
        deliver_handoffs(session, up, "Rain in Chennai on Friday")
        session.commit()
        session.refresh(down)
        assert down.observation["handoffs"][0]["from"] == "Up"
        assert down.runtime_state == "waiting" and down.next_run_at is not None
    factory.kw["bind"].dispose()


def test_graph_ignores_edges_to_missing_crew():
    a = CrewInstance(id="a", name="A", template_id="x", status="active", configuration_plan={})
    b = CrewInstance(
        id="b",
        name="B",
        template_id="x",
        status="draft",
        configuration_plan={"upstream": ["a", "gone"]},
    )
    graph = crew_graph([a, b])
    assert graph["edges"] == [{"from": "a", "to": "b"}]


def test_timeline_projects_reminders_and_lists_event_crews_as_standing():
    meals = meal_crew()
    mail = CrewInstance(
        id="m",
        name="Mail",
        template_id="communication",
        status="active",
        is_running=True,
        trigger_type="gmail_message",
    )
    now = datetime(2026, 11, 2, 8, 0, tzinfo=IST).astimezone(UTC)
    events, standing = upcoming_events([meals, mail], now, 1)
    assert [e["title"] for e in events][:2] == ["Breakfast", "Lunch"]
    assert all(not e["uses_model"] for e in events)
    assert standing == [
        {"crew_id": "m", "crew_name": "Mail", "label": "Runs when a matching email arrives"}
    ]


def test_pool_levels_gate_actions_and_setup(planning):
    client, auth, runtime, coordinator = planning
    pool = client.get("/api/v1/pool", headers=auth).json()
    gmail = next(r for r in pool["resources"] if r["id"] == "gmail")
    assert gmail["level"] == "write" and gmail["can_act"]
    assert all(c["status"] == "not_installed" for c in pool["community"])
    assert (
        client.put("/api/v1/pool/drive", headers=auth, json={"level": "write"}).status_code == 400
    )
    assert client.put("/api/v1/pool/nope", headers=auth, json={"level": "read"}).status_code == 404

    read_only = client.put("/api/v1/pool/gmail", headers=auth, json={"level": "read"}).json()
    assert read_only["level"] == "read"

    from mavo.resource_pool import ceiling_error

    with runtime.factory() as session:
        assert "read only" in ceiling_error(session, operation="gmail.send")
        assert "read only" in ceiling_error(session, capability="gmail:send")
        assert ceiling_error(session, capability="gmail:read") is None
        assert ceiling_error(session, operation="terminal.run") is None

    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Mail helper",
            "crew_proposal": {
                "template_id": "communication",
                "name": "Mail sender",
                "responsibilities": "Reply to mail",
                "trigger_type": "gmail_message",
                "requested_google_capabilities": ["gmail:read", "gmail:send"],
                "requested_actions": [{"operation": "gmail.send", "reason": "reply"}],
            },
        }
    )
    draft = turn(planning, "Reply to my mail")["draft"]
    assert any("read only" in item for item in draft["configuration_plan"]["missing"])

    client.put("/api/v1/pool/gmail", headers=auth, json={"level": "off"})
    with runtime.factory() as session:
        assert "off" in ceiling_error(session, capability="gmail:read")


def test_weather_level_uses_the_existing_resource_flags(planning):
    client, auth, _, _ = planning
    client.put("/api/v1/pool/weather", headers=auth, json={"level": "read"})
    assert client.get("/api/v1/resources/access", headers=auth).json()["weather"] is True
    client.put("/api/v1/pool/weather", headers=auth, json={"level": "off"})
    assert client.get("/api/v1/resources/access", headers=auth).json()["weather"] is False


def test_timeline_endpoint(planning):
    client, auth, _, _ = planning
    body = client.get("/api/v1/timeline?days=3", headers=auth).json()
    assert set(body) == {"generated_at", "days", "upcoming", "standing", "recent"}


def test_web_search_is_a_fixed_engine_page_load_under_the_sites_you_allow(monkeypatch):
    from types import SimpleNamespace

    import pytest
    from pydantic import ValidationError

    from mavo.action_rules import CATALOG, ActionRules
    from mavo.desktop_actions import DESKTOP_MODELS, SEARCH_HOST, BrowserSearch, search_url

    assert "browser.search" in CATALOG and "browser.search" in DESKTOP_MODELS
    with pytest.raises(ValidationError):
        BrowserSearch(query="")
    with pytest.raises(ValidationError):
        BrowserSearch(query="a\x00b", extra="https://evil.example")
    url = search_url("weather in Chennai & traffic")
    assert url.startswith(f"https://{SEARCH_HOST}/html/?q=") and "&+traffic" in url.replace(
        "%26", "&"
    )

    rules = ActionRules(None)
    rule = {
        "enabled": True,
        "mode": "restricted",
        "revision": "r",
        "domains": ["example.com"],
        "folders": [],
        "apps": [],
        "recipients": [],
        "repositories": [],
    }
    rules.read = lambda *a, **k: rule
    rules.paused = lambda session: False
    action = SimpleNamespace(
        operation="browser.search",
        crew_id=None,
        policy_revision="r",
        arguments={"query": "chennai weather"},
    )
    import mavo.resource_pool as pool

    monkeypatch.setattr(pool, "ceiling_error", lambda *a, **k: None)
    with pytest.raises(ValueError, match=SEARCH_HOST):
        rules.check(None, action)
    rule["domains"] = [SEARCH_HOST]
    assert rules.check(None, action) is rule


def test_sign_in_opens_a_normal_window_and_closes_cleanly(planning):
    from unittest.mock import AsyncMock, MagicMock

    client, auth, runtime, _ = planning
    desktop = runtime.actions.desktop
    state = client.get("/api/v1/browser", headers=auth).json()
    assert state["engine"] in {"Chrome", "Edge", "Brave"} and not state["open"]

    desktop.open_user_window = MagicMock()
    desktop.user_window_open = MagicMock(return_value=True)
    opened = client.post(
        "/api/v1/browser/sign-in", headers=auth, json={"url": "https://portal.example.com"}
    )
    assert opened.status_code == 200 and opened.json()["opened"]
    desktop.open_user_window.assert_called_once_with("https://portal.example.com")
    assert client.get("/api/v1/browser", headers=auth).json()["open"] is True
    assert (
        client.post(
            "/api/v1/browser/sign-in", headers=auth, json={"url": "http://127.0.0.1"}
        ).status_code
        == 422
    )

    desktop.close_user_window = AsyncMock()
    desktop.user_window_open = MagicMock(return_value=False)
    assert client.post("/api/v1/browser/close", headers=auth).json() == {"open": False}
    desktop.close_user_window.assert_awaited_once()

    desktop.open_user_window = MagicMock(side_effect=ValueError("A Crew is using the browser."))
    assert (
        client.post(
            "/api/v1/browser/sign-in", headers=auth, json={"url": "https://portal.example.com"}
        ).status_code
        == 409
    )
    client.post("/api/v1/actions/pause", headers=auth)
    assert (
        client.post(
            "/api/v1/browser/sign-in", headers=auth, json={"url": "https://portal.example.com"}
        ).status_code
        == 409
    )


def test_every_public_feed_is_selectable_and_unknown_ones_are_rejected(planning):
    from mavo.connectors.catalog import CAPABILITIES
    from mavo.resources.feed_catalog import FEEDS

    client, auth, _, _ = planning
    assert len(FEEDS) >= 12
    assert all(f"feeds:{key}:read" in CAPABILITIES for key in FEEDS)
    ok = client.post(
        "/api/v1/connectors/feeds/connect",
        headers=auth,
        json={"sources": ["hacker-news", "the-hindu", "bbc-world"]},
    )
    assert ok.status_code == 200 and ok.json()["connected"]
    assert {s["category"] for s in ok.json()["available_sources"]} >= {
        "News",
        "Technology",
        "Research",
    }
    assert (
        client.post(
            "/api/v1/connectors/feeds/connect", headers=auth, json={"sources": ["evil"]}
        ).status_code
        == 422
    )


def test_github_sign_in_retries_transient_failures_and_explains_the_rest():
    import asyncio

    import httpx
    import pytest

    from mavo.connectors.github_auth import GitHubConnection
    from mavo.connectors.hub import ConnectorError

    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"user_code": "ABCD-1234"})

    async def no_sleep(_):
        return None

    hub = type("Hub", (), {"transport": httpx.MockTransport(handler)})()
    connection = GitHubConnection(hub, {"client_id": "x", "app_slug": "y"})
    connection.sleep = no_sleep
    assert asyncio.run(connection.post("/login/device/code", {}))["user_code"] == "ABCD-1234"
    assert len(calls) == 3

    hub.transport = httpx.MockTransport(lambda r: httpx.Response(404))
    with pytest.raises(ConnectorError, match="404"):
        asyncio.run(connection.post("/login/device/code", {}))
    hub.transport = httpx.MockTransport(lambda r: httpx.Response(502))
    with pytest.raises(ConnectorError, match=r"could not be reached \(GitHub \(502\)\)"):
        asyncio.run(connection.post("/login/device/code", {}))


LOGIN_PAGE = """<html><body><form id="f" onsubmit="event.preventDefault(); window.sent = {u: u.value, p: p.value}">
<input id="search" type="text" placeholder="search"><input id="u" type="email"><input id="p" type="password">
<button type="submit">Sign in</button></form></body></html>"""


def test_saved_logins_never_leave_the_store_and_match_only_their_site(tmp_path):
    import pytest

    from mavo.credentials import MemoryCredentialStore
    from mavo.database import build_session_factory
    from mavo.site_logins import SiteLogins

    url = f"sqlite:///{(tmp_path / 'logins.db').as_posix()}"
    run_migrations(url)
    logins = SiteLogins(build_session_factory(url), MemoryCredentialStore())
    assert (
        logins.save("https://www.Placements.Haveloc.com/login", "student@example.com", "hunter2")
        == "placements.haveloc.com"
    )
    listing = logins.listing()
    assert listing[0]["host"] == "placements.haveloc.com"
    assert "hunter2" not in str(listing) and "student@example.com" not in str(listing)
    assert logins.lookup("placements.haveloc.com") == ("student@example.com", "hunter2")
    assert logins.lookup("a.placements.haveloc.com")
    assert logins.lookup("evilplacements.haveloc.com") is None
    assert logins.lookup("placements.haveloc.com.evil.io") is None
    for bad in ("127.0.0.1", "localhost", "intranet.local", "nodots"):
        with pytest.raises(ValueError):
            logins.save(bad, "u", "p")
    logins.delete("placements.haveloc.com")
    assert logins.lookup("placements.haveloc.com") is None and logins.listing() == []


def test_login_api_masks_everything_and_validates(planning):
    client, auth, _, _ = planning
    saved = client.put(
        "/api/v1/browser/logins",
        headers=auth,
        json={"site": "portal.example.com", "username": "alice@example.com", "password": "s3cret!"},
    )
    assert saved.status_code == 200 and saved.json()["host"] == "portal.example.com"
    assert "s3cret" not in saved.text and "alice@example.com" not in saved.text
    assert (
        client.get("/api/v1/browser/logins", headers=auth).json()[0]["host"] == "portal.example.com"
    )
    assert (
        client.put(
            "/api/v1/browser/logins",
            headers=auth,
            json={"site": "10.0.0.1", "username": "a", "password": "b"},
        ).status_code
        == 422
    )
    assert (
        client.delete("/api/v1/browser/logins/portal.example.com", headers=auth).status_code == 204
    )
    assert client.get("/api/v1/browser/logins", headers=auth).json() == []


def test_browser_fills_and_submits_the_saved_login_only_on_its_site():
    import asyncio

    import pytest

    from mavo.desktop_actions import DesktopActions, installed_browsers

    installed = installed_browsers()
    if not installed:
        pytest.skip("No Chromium browser installed")

    async def run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=next(iter(installed.values())), headless=True
            )
            page = await browser.new_page()
            await page.route(
                "https://portal.example.com/**",
                lambda r: r.fulfill(content_type="text/html", body=LOGIN_PAGE),
            )
            await page.route(
                "https://other.example.org/**",
                lambda r: r.fulfill(content_type="text/html", body=LOGIN_PAGE),
            )
            desktop = DesktopActions("unused")
            desktop.page = page
            desktop.login_lookup = lambda host: (
                ("alice@example.com", "s3cret") if host == "portal.example.com" else None
            )
            await page.goto("https://other.example.org/")
            assert await desktop.autofill() is False
            assert await page.evaluate("window.sent === undefined")
            await page.goto("https://portal.example.com/login")
            assert await desktop.autofill() is True
            assert await page.evaluate("window.sent") == {"u": "alice@example.com", "p": "s3cret"}
            assert await page.input_value("#search") == ""
            await browser.close()

    asyncio.run(run())


SLOW_PAGE = """<html><body><h1 id="n">0 Jobs Listed</h1>
<script>setTimeout(() => { document.getElementById('n').textContent = '108 Jobs Listed'; }, 2200)</script></body></html>"""
CAPTCHA_PAGE = (
    "<html><body><h1>Verify you're human</h1><p>Please complete the verification.</p></body></html>"
)
ERROR_PAGE = "<html><body><p>Failed to load jobs.</p></body></html>"


def context_of(page):
    return page.context


def with_page(html, work):
    import asyncio

    import pytest

    from mavo.desktop_actions import DesktopActions, installed_browsers

    installed = installed_browsers()
    if not installed:
        pytest.skip("No Chromium browser installed")

    async def run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=next(iter(installed.values())), headless=True
            )
            page = await browser.new_page()
            await page.route(
                "https://site.example.com/**",
                lambda r: r.fulfill(content_type="text/html", body=html),
            )
            await page.goto("https://site.example.com/")
            desktop = DesktopActions("unused")
            desktop.page = page
            try:
                return await work(desktop, page)
            finally:
                await browser.close()

    return asyncio.run(run())


def test_a_slow_page_is_read_after_it_has_finished_not_while_it_shows_zero():
    async def work(desktop, page):
        waited = await desktop.settle(12)
        return waited, await page.inner_text("body")

    waited, text = with_page(SLOW_PAGE, work)
    assert "108 Jobs Listed" in text and waited >= 2


def test_logins_and_human_checks_are_reported_not_mistaken_for_empty_results():
    async def wall(desktop, page):
        return await desktop.sign_in_wall()

    captcha = with_page(CAPTCHA_PAGE, wall)
    assert (captcha["kind"], captcha["host"]) == ("captcha", "site.example.com")
    login = with_page(LOGIN_PAGE, wall)
    assert (login["kind"], login["host"]) == ("login", "site.example.com")
    # The person is sent to the page that holds the form, on the same site.
    assert login["login_url"] == login["url"] == "https://site.example.com/"
    assert with_page("<html><body>Welcome back</body></html>", wall) is None


def test_an_ended_session_is_a_sign_in_wall_and_points_at_the_sign_in_link():
    async def wall(desktop, page):
        return await desktop.sign_in_wall()

    ended = "<html><body><h1>Jobs</h1><p>Your session has timed out. Please sign in again.</p></body></html>"
    assert with_page(ended, wall)["kind"] == "login"
    linked = "<html><body><p>Session expired</p><a href='https://site.example.com/auth/start'>Log in</a><a href='https://evil.example.org/x'>Sign in</a></body></html>"
    assert with_page(linked, wall)["login_url"] == "https://site.example.com/auth/start"
    other_site_only = "<html><body><p>Session expired</p><a href='https://evil.example.org/x'>Sign in</a></body></html>"
    assert with_page(other_site_only, wall)["login_url"] == "https://site.example.com/"
    signed_in = (
        "<html><body><p>Your last session expired yesterday.</p><a>Sign out</a></body></html>"
    )
    assert with_page(signed_in, wall) is None
    article = (
        "<html><body><h1>Cookies</h1><p>A cookie expires when the browser closes.</p></body></html>"
    )
    assert with_page(article, wall) is None


def test_the_sign_in_window_opens_on_the_sites_own_login_page_only():
    from mavo.trial import sign_in_target

    wall = {"kind": "login", "host": "p.example.com"}
    assert (
        sign_in_target({**wall, "login_url": "https://p.example.com/login?next=/jobs"})
        == "https://p.example.com/login?next=/jobs"
    )
    assert (
        sign_in_target(
            {
                **wall,
                "login_url": "https://evil.example.org/login",
                "url": "https://p.example.com/x",
            }
        )
        == "https://p.example.com/x"
    )
    assert (
        sign_in_target({**wall, "login_url": "http://p.example.com/login"})
        == "https://p.example.com/"
    )
    assert sign_in_target(wall) == "https://p.example.com/"


def test_site_scope_covers_the_sites_own_subdomains_but_not_other_sites():
    from mavo.browser_proxy import same_site

    allowed = ["placements.haveloc.com"]
    assert same_site("placements.haveloc.com", allowed)
    assert same_site("api.haveloc.com", allowed)
    assert not same_site("haveloc.com.evil.io", allowed)
    assert not same_site("evilhaveloc.com", allowed)
    assert same_site("api.shop.co.uk", ["www.shop.co.uk"])
    assert not same_site("other.co.uk", ["www.shop.co.uk"])


def test_sample_run_reports_what_happened_and_changes_nothing(planning):
    from unittest.mock import AsyncMock, MagicMock

    client, auth, runtime, _ = planning
    route = client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "t"}
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "Portal",
            "route_id": route["id"],
            "responsibilities": "Every 30 minutes read https://portal.example.com and list new jobs",
            "trigger_type": "interval",
        },
    ).json()
    pages = {
        "browser.open": {"url": "https://portal.example.com/jobs", "title": "Jobs"},
        "browser.read": {
            "url": "https://portal.example.com/jobs",
            "title": "Jobs",
            "text": "108 Jobs Listed " * 40,
            "waited_seconds": 2.1,
        },
    }
    runtime.actions.desktop.browser = AsyncMock(side_effect=lambda op, *a, **k: pages[op])
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {"state": "wait_for_next_review", "summary": "3 new jobs", "next_step": "wait"}
        )
    )
    out = client.post(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()
    assert out["verdict"] == "ok" and out["result"] == "3 new jobs" and out["model_calls"] == 1
    assert [s["label"] for s in out["steps"]] == [
        "Opened the site",
        "Read the page",
        "Produced the result",
    ]
    assert out["trail"] == ["Opened portal.example.com", "Read it and wrote the result"]
    after = client.get("/api/v1/crew", headers=auth).json()[0]
    assert (
        after["daily_calls_used"] == 0
        and client.get("/api/v1/reminders", headers=auth).json() == []
    )

    # The page looks like a login, but the AGENT decides what that means.
    desktop = runtime.actions.desktop
    desktop.open_user_window = MagicMock()
    desktop.user_window_open = MagicMock(return_value=False)
    pages["browser.read"] = {
        **pages["browser.read"],
        "sign_in_required": {
            "kind": "login",
            "host": "portal.example.com",
            "login_url": "https://portal.example.com/login?next=/jobs",
        },
    }

    def said(**fields):
        return AsyncMock(
            return_value=json.dumps(
                {"state": "wait_for_next_review", "next_step": "wait", **fields}
            )
        )

    # 1. The agent can still do the job: it is not stopped and nothing is opened.
    runtime.router.chat = said(summary="3 new jobs")
    worked = client.post(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()
    assert worked["verdict"] == "ok" and worked["result"] == "3 new jobs"
    assert worked["sign_in_required"] is None and worked["model_calls"] == 1
    assert "SIGN-IN HINT" in runtime.router.chat.call_args.args[2][0]["content"]
    desktop.open_user_window.assert_not_called()

    # 2. The agent looks and says it needs you to sign in: the browser opens on the login page.
    runtime.router.chat = said(
        state="waiting_for_input",
        summary="This is a login page.",
        question="Please sign in to portal.example.com",
    )
    blocked = client.post(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()
    assert blocked["verdict"] == "sign_in" and blocked["model_calls"] == 1
    assert blocked["headline"] == "Please sign in" and blocked["signing_in"] is True
    desktop.open_user_window.assert_called_once_with("https://portal.example.com/login?next=/jobs")

    # 3. Run again while that window is still open: wait for it, do not open a second one.
    desktop.open_user_window.reset_mock()
    desktop.user_window_open = MagicMock(return_value=True)
    waiting = client.post(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()
    assert waiting["verdict"] == "sign_in" and waiting["signing_in"] is True
    desktop.open_user_window.assert_not_called()
    desktop.user_window_open = MagicMock(return_value=False)

    # 4. Any other question from the agent is passed on; no browser is opened for it.
    runtime.router.chat = said(
        state="waiting_for_input", summary="Unsure.", question="Which department do you mean?"
    )
    asked = client.post(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()
    assert asked["verdict"] == "needs_input" and asked["result"] == "Which department do you mean?"
    assert asked["sign_in_required"] is None
    desktop.open_user_window.assert_not_called()

    # 5. The agent can decide on a sign-in the application never noticed.
    pages["browser.read"].pop("sign_in_required")
    runtime.router.chat = said(
        state="waiting_for_input", summary="Locked.", question="Please log in to see the jobs"
    )
    unseen = client.post(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()
    assert unseen["verdict"] == "sign_in" and unseen["signing_in"] is True
    desktop.open_user_window.assert_called_once_with("https://portal.example.com/jobs")

    # 6. In the app the run happens in the background and is followed by asking how far it is.
    import time

    desktop.open_user_window.reset_mock()
    pages["browser.read"]["sign_in_required"] = {"kind": "login", "host": "portal.example.com"}
    runtime.router.chat = said(summary="3 new jobs")
    started = client.post(f"/api/v1/crew/{crew['id']}/trial/start", headers=auth).json()
    assert started["status"] in {"running", "done"}
    for _ in range(100):
        status = client.get(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()
        if status["status"] == "done":
            break
        time.sleep(0.1)
    assert status["status"] == "done" and status["result"]["result"] == "3 new jobs"
    assert client.get("/api/v1/crew/nobody/trial", headers=auth).json() == {"status": "none"}

    runtime.actions.desktop.browser = AsyncMock(
        side_effect=ValueError("Mavo could not reach the site.")
    )
    assert (
        client.post(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()["verdict"]
        == "unreachable"
    )

    reminder = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "R",
            "route_id": route["id"],
            "responsibilities": "Remind me at 9",
            "schedule_time": "09:00",
        },
    ).json()
    assert (
        client.post(f"/api/v1/crew/{reminder['id']}/trial", headers=auth).json()["supported"]
        is False
    )


def test_a_stalled_sample_run_retries_once_and_shows_the_path_the_agent_took(planning):
    from unittest.mock import AsyncMock

    client, auth, runtime, _ = planning
    route = client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "t"}
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "Credits",
            "route_id": route["id"],
            "responsibilities": "Every 30 minutes read https://portal.example.com credits",
            "trigger_type": "interval",
        },
    ).json()
    stalled = ValueError("The page stopped responding, so Mavo closed it. Try again in a moment.")
    seen = {"opens": 0, "always_stall": False, "moved": False}

    async def browser(operation, args, rule, owner, scope_guard=None, limit=None):
        if operation == "browser.open":
            seen["opens"] += 1
            assert limit and limit <= 90  # a sample run never waits the usual two minutes
            if seen["always_stall"] or seen["opens"] == 1:
                raise stalled
            return {"url": "https://portal.example.com/", "title": "Home"}
        if operation == "browser.navigate":
            seen["moved"] = True
            steps = [{"clicked": "menu"}, {"opened": "Credits"}]
            return {"navigation": {"reached": "Credits", "steps": steps}}
        assert operation == "browser.read"
        where = "/settings/credits" if seen["moved"] else "/"
        return {"url": "https://portal.example.com" + where, "title": "T", "text": "x " * 200}

    runtime.actions.desktop.browser = browser
    go = {"operation": "browser.navigate", "arguments": {"goal": "credits"}}
    runtime.router.chat = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": "Looking.",
                    "next_step": "go",
                    "actions": [go],
                }
            ),
            json.dumps({"state": "wait_for_next_review", "summary": "$0.74", "next_step": "wait"}),
        ]
    )
    out = client.post(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()
    assert seen["opens"] == 2  # one stall, then a second look from a clean browser
    assert out["verdict"] == "ok" and out["result"] == "$0.74"
    assert out["trail"] == [
        "Opened portal.example.com",
        "Found credits (menu → Credits)",
        "Read it and wrote the result",
    ]

    # A site that keeps stalling is given up on after the second look, with the plain reason.
    seen.update(opens=0, always_stall=True, moved=False)
    gave_up = client.post(f"/api/v1/crew/{crew['id']}/trial", headers=auth).json()
    assert seen["opens"] == 2
    assert gave_up["verdict"] == "unreachable" and "stopped responding" in gave_up["detail"]


def test_a_sample_run_tells_an_unreadable_answer_from_a_dead_connection(planning):
    from unittest.mock import AsyncMock

    from mavo.model_router import ModelProviderError

    client, auth, runtime, _ = planning
    route = client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "t"}
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "Portal",
            "route_id": route["id"],
            "responsibilities": "Every 30 minutes read https://portal.example.com",
            "trigger_type": "interval",
        },
    ).json()
    page = {"url": "https://portal.example.com/", "title": "Jobs", "text": "108 Jobs " * 40}

    async def browser(operation, *args, **kwargs):
        return page

    runtime.actions.desktop.browser = browser
    trial = f"/api/v1/crew/{crew['id']}/trial"

    # A final answer that leaves "next_step" null is a result, not a failure.
    finished = json.dumps({"state": "wait_for_next_review", "summary": "3 jobs", "next_step": None})
    runtime.router.chat = AsyncMock(return_value=finished)
    ok = client.post(trial, headers=auth).json()
    assert ok["verdict"] == "ok" and ok["result"] == "3 jobs"

    # Words that are not a result: says so, and shows no result.
    runtime.router.chat = AsyncMock(return_value="I looked but cannot say.")
    unreadable = client.post(trial, headers=auth).json()
    assert unreadable["verdict"] == "agent" and unreadable["result"] == ""
    assert unreadable["headline"] == "The agent's answer could not be read"
    assert "another model" in unreadable["detail"]

    # A connection that fails is a different problem with a different remedy.
    runtime.router.chat = AsyncMock(side_effect=ModelProviderError("down"))
    dead = client.post(trial, headers=auth).json()
    assert dead["headline"] == "The agent connection did not answer"
    assert "Settings" in dead["detail"] and dead["result"] == ""


NAV_SITE = {
    "/": """<html><body><header><a href="/">Home</a><a href="/pricing">Pricing</a>
      <button id="av" aria-haspopup="menu" aria-expanded="false" onclick="
        document.getElementById('m').hidden = false; this.setAttribute('aria-expanded','true')">
        <img alt="" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></button>
      <nav id="m" hidden><a href="/settings">Settings</a><a href="/billing/credits">Credits</a>
      <a href="/logout">Sign out</a><button onclick="document.title='DELETED'">Delete account</button></nav>
      </header><h1>Dashboard</h1></body></html>""",
    "/pricing": "<html><body><h1>Pricing</h1></body></html>",
    "/settings": "<html><body><h1>Settings</h1></body></html>",
    "/billing/credits": "<html><body><h1>Credits</h1><p>Balance $0.15</p></body></html>",
}


def navigate_site(goal, site=None):
    from urllib.parse import urlsplit

    site = site or NAV_SITE

    async def work(desktop, page):
        await page.route(
            "https://app.example.com/**",
            lambda r: r.fulfill(
                content_type="text/html",
                body=site.get(urlsplit(r.request.url).path, "<html><body>missing</body></html>"),
            ),
        )
        await page.goto("https://app.example.com/")
        result = await desktop.navigate(goal)
        return result, page.url, await page.title()

    return with_page("<html></html>", work)


def test_navigation_finds_a_visible_link_by_its_name():
    result, url, _ = navigate_site("pricing")
    assert result["reached"] and url.endswith("/pricing")
    assert [s["clicked"] for s in result["steps"]] == ["Pricing"]


def test_navigation_opens_the_account_menu_to_find_a_hidden_page():
    result, url, _ = navigate_site("credits")
    assert result["reached"] and url.endswith("/billing/credits")
    assert [list(s)[0] for s in result["steps"]] == ["opened", "clicked"]


def test_navigation_uses_synonyms_and_never_clicks_destructive_controls():
    result, url, title = navigate_site("balance")
    assert url.endswith("/billing/credits")
    unreachable, url, title = navigate_site("account")
    assert title != "DELETED" and "logout" not in url
    assert not [s for s in unreachable["steps"] if "Delete" in str(s) or "Sign out" in str(s)]


# A "billing@..." mail link, a phone link and a button showing an address all share words with the
# goal "billing". Following one would start the person's mail or phone app instead of a page.
MAIL_SITE = {
    "/": """<html><body><header><a href="/">Home</a><a href="/credits">Credits</a>
      <a href="mailto:billing@app.example.com" onclick="document.title='MAILED'">billing@app.example.com</a>
      <a href="tel:+911234567890" onclick="document.title='CALLED'">Billing helpline</a>
      <button onclick="document.title='MAILED'">billing@app.example.com</button></header>
      <h1>Dashboard</h1></body></html>""",
    "/credits": "<html><body><h1>Billing</h1><p>Balance $0.68</p></body></html>",
}


def test_navigation_never_follows_a_mail_or_phone_link_that_shares_a_word_with_the_goal():
    result, url, title = navigate_site("billing", MAIL_SITE)
    assert title not in {"MAILED", "CALLED"}
    assert result["reached"] and url.endswith("/credits")
    assert [s["clicked"] for s in result["steps"]] == ["Credits"]
    # With the real page there is nothing else to find: it must stop, not click the address.
    nothing, url, title = navigate_site("billing", {"/": MAIL_SITE["/"].replace("/credits", "/x")})
    assert title not in {"MAILED", "CALLED"}
    assert not [s for s in nothing["steps"] if "@" in str(s) or "helpline" in str(s)]


def test_links_that_start_another_program_are_not_scored_or_followed():
    from mavo.desktop_actions import DesktopActions

    def item(label="", href=""):
        return {"label": label, "aria": "", "href": href}

    for bad in (
        item("billing@airouter.in", "mailto:billing@airouter.in"),
        item("Billing helpline", "tel:+911234567890"),
        item("Billing", "ms-settings:network"),
        item("billing@airouter.in"),
    ):
        assert DesktopActions.nav_score("billing", bad) == 0
    assert DesktopActions.nav_score("billing", item("Billing", "/settings/billing")) > 0
    assert DesktopActions.nav_score("billing", item("Billing", "https://app.example.com/b")) > 0

    html = """<html><body><a id="m" href="mailto:a@b.co"><span id="in">Mail us</span></a>
      <a id="t" href="tel:123">Call</a><a id="w" href="/credits"><b id="wi">Credits</b></a>
      <a id="j" href="javascript:void(0)">Menu</a>
      <svg><a id="s" href="/credits"><text y="10">Credits</text></a></svg></body></html>"""

    async def probe(desktop, page):
        return {
            key: await page.locator(f"#{key}").evaluate(DesktopActions.OPENS_APP)
            for key in ("m", "in", "t", "w", "wi", "j", "s")
        }

    opens = with_page(html, probe)
    assert opens == {
        "m": True,
        "in": True,
        "t": True,
        "w": False,
        "wi": False,
        "j": False,
        "s": False,
    }

    # The page scan never lists them either.
    async def listed(desktop, page):
        return [entry["label"] for entry in await desktop.scan()]

    labels = with_page(html, listed)
    assert "Credits" in labels and "Menu" in labels
    assert "Mail us" not in labels and "Call" not in labels


# "Billing" leads to a page called "Credits". Arriving there must end the search: the buttons on it
# ("Add Credits" starts a purchase) are not part of finding a page.
PAY_SITE = {
    "/": """<html><body><header><a href="/">Home</a><a href="/settings/credits">Credits</a></header>
      <h1>Dashboard</h1></body></html>""",
    "/settings/credits": """<html><body><nav><a href="/">Home</a><a href="/settings/credits">Credits</a>
      <button onclick="document.title='REFRESHED'">Refresh credits</button>
      <button onclick="document.title='CHARGING'">Add Credits</button>
      <button onclick="document.title='CHARGING'">Recharge</button></nav>
      <h1>Your balance</h1><p>$0.68</p></body></html>""",
}


def test_navigation_stops_on_a_page_named_by_a_synonym_and_never_starts_a_purchase():
    from mavo.desktop_actions import BrowserNavigate, DesktopActions

    result, url, title = navigate_site("billing", PAY_SITE)
    assert url.endswith("/settings/credits") and result["reached"]
    assert [s["clicked"] for s in result["steps"]] == ["Credits"]
    assert title not in {"REFRESHED", "CHARGING"}

    def item(label):
        return {"label": label, "aria": "", "href": ""}

    for money in ("Add Credits", "Add funds", "Recharge", "Upgrade plan", "Renew"):
        assert DesktopActions.nav_score("credits", item(money)) == 0, money
    assert DesktopActions.nav_score("credits", item("Credits")) > 0
    for goal in ("add credits", "recharge my account"):
        try:
            BrowserNavigate(goal=goal)
        except ValueError:
            continue
        raise AssertionError(f"{goal!r} should be refused as a goal")


DRAG_PAGE = """<html><body style="margin:0">
<div id="card" draggable="true" style="width:80px;height:40px;background:#9cf">Card</div>
<div id="zone" style="margin-top:30px;width:200px;height:80px;background:#eee">Drop here</div>
<div id="track" style="position:relative;margin-top:30px;width:300px;height:20px;background:#ccc">
  <div id="knob" style="position:absolute;left:0;top:0;width:20px;height:20px;background:#333"></div></div>
<div id="menu" style="margin-top:30px">Menu</div><div id="sub" hidden>Settings</div>
<button id="dbl" style="margin-top:30px">Open</button>
<a id="mail" href="mailto:a@b.co" style="display:block;margin-top:30px">Write to us</a>
<script>
const card = document.getElementById('card'), zone = document.getElementById('zone');
card.addEventListener('dragstart', e => e.dataTransfer.setData('text', 'card'));
zone.addEventListener('dragover', e => e.preventDefault());
zone.addEventListener('drop', e => { e.preventDefault(); zone.textContent = 'Dropped ' + e.dataTransfer.getData('text'); });
let down = false; const knob = document.getElementById('knob'), track = document.getElementById('track');
knob.addEventListener('mousedown', () => down = true);
document.addEventListener('mouseup', () => down = false);
document.addEventListener('mousemove', e => { if (down) {
  const left = Math.max(0, Math.min(280, e.clientX - track.getBoundingClientRect().left - 10));
  knob.style.left = left + 'px'; knob.dataset.value = Math.round(left); } });
document.getElementById('menu').addEventListener('mouseenter', () => document.getElementById('sub').hidden = false);
document.getElementById('dbl').addEventListener('click', () => document.title = 'single');
document.getElementById('dbl').addEventListener('dblclick', () => document.title = 'DOUBLE');
document.getElementById('mail').addEventListener('click', () => document.title = 'MAILED');
</script></body></html>"""
SITE_RULE = {"mode": "restricted", "domains": ["site.example.com"]}


def test_hover_double_click_and_drag_work_on_a_real_page():
    async def work(desktop, page):
        async def act(operation, **args):
            return await desktop.browser(operation, args, SITE_RULE, "t")

        await act("browser.drag", selector="#card", to_selector="#zone")
        dropped = await page.locator("#zone").inner_text()
        await act("browser.drag", selector="#knob", dx=120, dy=0)
        value = int(await page.locator("#knob").get_attribute("data-value"))
        was_hidden = await page.locator("#sub").is_hidden()
        await act("browser.hover", selector="#menu")
        shown = await page.locator("#sub").is_visible()
        await act("browser.double_click", selector="#dbl")
        return dropped, value, was_hidden, shown, await page.title()

    dropped, value, was_hidden, shown, title = with_page(DRAG_PAGE, work)
    assert dropped == "Dropped card"  # dragged onto another element
    assert 117 <= value <= 123  # dragged by pixels, as a slider is
    assert was_hidden and shown  # the menu only exists while hovering
    assert title == "DOUBLE"  # two clicks, then the double-click


HOP_PAGE = """<html><body>Working on it...<script>
if (location.pathname.startsWith('/oauth2')) setTimeout(() => location.replace('/dashboard'), 2500);
</script></body></html>"""
HOP_FORM = (
    """<html><body><h1>Sign in</h1><input type="password" aria-label="Password"></body></html>"""
)


def test_a_sign_in_that_is_only_passing_through_is_waited_for_and_a_real_one_is_not():
    """Opening Microsoft Forms handed the agent `login.microsoftonline.com/.../authorize` mid-hop (the
    saved session was about to send it on), and it reported "needs sign-in" about a working login."""

    async def hop(desktop, page):
        await page.goto("https://site.example.com/oauth2/v2.0/authorize")
        await desktop.wait_out_redirects()
        return page.url

    assert with_page(HOP_PAGE, hop) == "https://site.example.com/dashboard"

    async def form(desktop, page):
        import time

        await page.goto("https://site.example.com/oauth2/v2.0/authorize")
        began = time.monotonic()
        await desktop.wait_out_redirects()
        return page.url, time.monotonic() - began

    url, took = with_page(HOP_FORM, form)
    assert url.endswith("/oauth2/v2.0/authorize") and took < 2  # a real form: no waiting


TABS_PAGE = """<html><body>
<button id="designer" onclick="window.open('/designer')">New Form</button>
<button id="elsewhere" onclick="window.open('https://evil.test/')">Offsite</button>
<button id="popup" onclick="window.open('/approve')">Approve in a window</button>
</body></html>"""


def test_a_tab_a_site_opens_is_followed_on_an_allowed_site_and_closed_anywhere_else():
    """Microsoft Forms' "New Form" opens the designer in a new tab. It used to be closed at once, so
    the click looked like it did nothing and the agent read the old page for ever."""

    async def work(desktop, page):
        import asyncio

        context = context_of(page)

        def serve(url, body):
            return context.route(url, lambda r: r.fulfill(content_type="text/html", body=body))

        await serve(
            "https://site.example.com/designer",
            "<html><title>Designer</title><body><h1>Untitled form</h1></body></html>",
        )
        await serve(
            "https://site.example.com/approve",
            "<html><title>Approve</title><body>ok<script>setTimeout(() => window.close(), 5000)"
            "</script></body></html>",
        )
        await serve("https://evil.test/", "<html><title>Evil</title><body>x</body></html>")
        desktop.context, desktop.browser_owner = context, "t"
        desktop.track_tabs()

        async def act(operation, **args):
            return await desktop.browser(operation, args, SITE_RULE, "t")

        offsite = await act("browser.click", selector="#elsewhere")
        await asyncio.sleep(0.5)
        refused = ("new_tab" not in offsite, desktop.page is page, len(context.pages))
        opened = await act("browser.click", selector="#designer")
        followed = (
            "new_tab" in opened,
            opened["url"].endswith("/designer"),
            opened["title"],
            desktop.page is not page,
        )
        await desktop.page.close()  # the person closes the designer: back to the page before it
        await asyncio.sleep(0.3)
        back = desktop.page is page
        await act("browser.click", selector="#popup")  # a window that closes itself, like a sign-in
        during = desktop.page is not page
        await asyncio.sleep(6)
        return refused, followed, back, during, desktop.page is page

    refused, followed, back, during, after = with_page(TABS_PAGE, work)
    assert refused == (True, True, 1)  # off the allowed sites: closed, and the page is unchanged
    assert followed == (True, True, "Designer", True)  # on the allowed site: it is now the page
    assert back and during and after


def test_the_new_click_actions_refuse_a_link_that_opens_another_program():
    import pytest

    from mavo.desktop_actions import BrowserElement

    async def work(desktop, page):
        with pytest.raises(ValueError, match="another program"):
            await desktop.browser("browser.double_click", {"selector": "#mail"}, SITE_RULE, "t")
        return await page.title()

    assert with_page(DRAG_PAGE, work) != "MAILED"
    assert BrowserElement(selector="#mail").selector == "#mail"


def test_a_drag_needs_one_destination_and_names_what_is_missing():
    import pytest
    from pydantic import ValidationError

    from mavo.desktop_actions import BrowserDrag

    for bad in (
        {"ref": "e1"},
        {"ref": "e1", "to_ref": "e2", "dx": 5},
        {"ref": "e1", "to_ref": "e2", "to_selector": "#x"},
        {"to_ref": "e2"},
        {"ref": "e1", "dx": 5000},
    ):
        with pytest.raises(ValidationError):
            BrowserDrag(**bad)
    assert BrowserDrag(ref="e1", to_ref="f1_e2").to_ref == "f1_e2"
    assert BrowserDrag(selector="#knob", dx=-40).dy is None

    async def gone(desktop, page):
        for args, message in (
            ({"selector": "#nope", "dx": 5}, "gone"),
            ({"selector": "#card", "to_selector": "#nope"}, "drop target is gone"),
        ):
            with pytest.raises(ValueError, match=message):
                await desktop.drag(args)

    with_page(DRAG_PAGE, gone)


def test_navigation_says_so_when_the_page_does_not_exist():
    result, url, _ = navigate_site("quarterly tax filings")
    assert result["reached"] is False and url == "https://app.example.com/"


def test_destructive_goals_are_refused_up_front():
    import pytest
    from pydantic import ValidationError

    from mavo.desktop_actions import BrowserNavigate

    for goal in ("delete account", "sign out", "buy credits", "top up"):
        with pytest.raises(ValidationError):
            BrowserNavigate(goal=goal)
    assert BrowserNavigate(goal="  Credits  ").goal == "Credits"


def test_single_sign_on_pages_count_as_a_sign_in_wall():
    async def wall(desktop, page):
        return await desktop.sign_in_wall()

    sso = "<html><body><h1>Welcome</h1><button>Continue with Google</button></body></html>"
    assert with_page(sso, wall)["kind"] == "login"
    home = "<html><body><h1>Dashboard</h1><div><a>Sign out</a></div><button>Continue with Google</button></body></html>"
    assert with_page(home, wall) is None


def test_a_logged_out_redirect_to_the_front_page_is_a_sign_in_wall():
    home = "<html><body><header><a href='/models'>Models</a><a href='/signin'>Sign In</a></header><h1>One API</h1></body></html>"

    async def redirected(desktop, page):
        desktop.open_redirected = True
        return await desktop.sign_in_wall()

    async def ordinary(desktop, page):
        desktop.open_redirected = False
        return await desktop.sign_in_wall()

    found = with_page(home, redirected)
    assert found["kind"] == "login" and found["login_url"] == "https://site.example.com/signin"
    assert with_page(home, ordinary) is None
    signed_in = "<html><body><header><a href='/m'>Models</a><a>Sign In</a><div><a>Sign out</a></div></header></body></html>"
    assert with_page(signed_in, redirected) is None


AGENT_PAGE = """<html><body><h1>Jobs</h1>
<label for="q">Search</label><input id="q" type="text" placeholder="Search jobs">
<select aria-label="Per page"><option>10</option><option>20</option><option>50</option></select>
<input type="password" aria-label="Password" value="hunter2">
<table><thead><tr><th>Title</th><th>Status</th></tr></thead><tbody>
<tr><td>Acme Backend Intern</td><td>Open For Applications</td></tr>
<tr><td>Globex Analyst</td><td>Closed For Applications</td></tr></tbody></table>
<button id="more" onclick="document.getElementById('extra').hidden=false; this.setAttribute('aria-expanded','true')" aria-expanded="false">Show more</button>
<div id="extra" hidden><a href="/jobs/3">Initech Trainee</a></div>
<input id="out" type="text" aria-label="Output" value="">
<div style="height:3000px"></div><div id="end">The end</div>
<script>document.addEventListener('keydown', e => { if (e.key === 'Enter') document.getElementById('out').value = 'entered'; });</script>
</body></html>"""


def test_a_page_read_gives_tables_and_exact_refs_to_act_on():
    async def work(desktop, page):
        desktop.page = page
        snapshot = await page.evaluate(desktop.SNAPSHOT)
        return snapshot, desktop.view_of(snapshot, page.url, "Jobs", "Jobs text")

    snapshot, view = with_page(AGENT_PAGE, work)
    table = snapshot["tables"][0]
    assert table["headers"] == ["Title", "Status"]
    assert table["rows"] == [
        ["Acme Backend Intern", "Open For Applications"],
        ["Globex Analyst", "Closed For Applications"],
    ]
    by_name = {e["name"]: e for e in snapshot["elements"]}
    assert by_name["Search"]["role"] == "textbox" and by_name["Per page"]["options"] == [
        "10",
        "20",
        "50",
    ]
    assert by_name["Show more"]["expanded"] is False
    assert "value" not in by_name["Password"]  # a password's value never reaches a model
    assert "hunter2" not in view and "TABLE 1 (2 rows): Title | Status" in view
    assert "1. Acme Backend Intern | Open For Applications" in view
    assert len({e["ref"] for e in snapshot["elements"]}) == len(snapshot["elements"])


def test_the_agent_can_click_fill_select_press_and_scroll_by_ref():
    async def work(desktop, page):
        desktop.page = page
        desktop.active_unrestricted = True
        desktop.active_domains = []
        snapshot = await page.evaluate(desktop.SNAPSHOT)
        ref = {e["name"]: e["ref"] for e in snapshot["elements"]}
        # the same code path the browser tool uses, minus the launch
        await desktop.target({"ref": ref["Search"]}).fill("backend")
        await desktop.target({"ref": ref["Per page"]}).select_option(label="50")
        await desktop.target({"ref": ref["Show more"]}).click()
        await desktop.target({"selector": "#out"}).focus()
        await page.keyboard.press("Enter")
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        return {
            "search": await page.input_value("#q"),
            "per_page": await page.eval_on_selector("select", "s => s.value"),
            "extra_visible": await page.is_visible("#extra"),
            "out": await page.input_value("#out"),
            "scrolled": await page.evaluate("window.scrollY > 100"),
        }

    assert with_page(AGENT_PAGE, work) == {
        "search": "backend",
        "per_page": "50",
        "extra_visible": True,
        "out": "entered",
        "scrolled": True,
    }


def test_actions_need_exactly_one_target_and_never_fill_passwords():
    import pytest
    from pydantic import ValidationError

    from mavo.desktop_actions import DESKTOP_MODELS

    click = DESKTOP_MODELS["browser.click"]
    assert click(ref="e12").ref == "e12" and click(selector="#go").selector == "#go"
    for bad in ({}, {"ref": "e1", "selector": "#a"}, {"ref": "12"}, {"ref": "e99999"}):
        with pytest.raises(ValidationError):
            click(**bad)
    assert DESKTOP_MODELS["browser.select"](ref="e3", option="50").option == "50"
    assert DESKTOP_MODELS["browser.press"](key="Enter").key == "Enter"
    with pytest.raises(ValidationError):
        DESKTOP_MODELS["browser.press"](key="F12")
    assert DESKTOP_MODELS["browser.wait"](seconds=3, text="Credits").text == "Credits"
    assert (
        DESKTOP_MODELS["browser.read"](scroll=True).scroll is True
        and DESKTOP_MODELS["browser.read"]().scroll is False
    )


def test_a_model_is_shown_the_compact_page_view_not_raw_json():
    from types import SimpleNamespace

    from mavo.crew_workers import action_memo

    item = SimpleNamespace(
        operation="browser.read",
        status="completed",
        error=None,
        result={
            "view": "TABLE 1 (2 rows)\n" + "x" * 50000,
            "text": "y" * 12000,
            "elements": [{"ref": "e1"}] * 150,
        },
    )
    memo = action_memo(item, 1000)
    assert set(memo["result"]) == {"view"} and len(memo["result"]["view"]) == 1000
    wall = SimpleNamespace(
        operation="browser.read",
        status="completed",
        error=None,
        result={"view": "v", "sign_in_required": {"kind": "login", "host": "h"}},
    )
    assert action_memo(wall, 100)["result"]["sign_in_required"]["kind"] == "login"
    plain = SimpleNamespace(
        operation="gmail.send", status="completed", error=None, result={"id": 1}
    )
    assert action_memo(plain, 100)["result"] == {"id": 1}


# ── a sign-in check that can never hang, and a browser that cannot be held forever ──


class FakeContext:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_a_page_that_never_answers_is_closed_and_releases_the_browser(tmp_path, monkeypatch):
    import asyncio

    import pytest

    from mavo import desktop_actions
    from mavo.desktop_actions import DesktopActions

    monkeypatch.setitem(desktop_actions.BROWSER_LIMITS, "browser.read", 0.2)
    desktop = DesktopActions(tmp_path)
    context = FakeContext()

    async def stuck(*args, **kwargs):
        desktop.context, desktop.browser_owner = context, "crew"
        await asyncio.sleep(30)

    desktop._browser = stuck

    async def run():
        with pytest.raises(ValueError, match="stopped responding"):
            await desktop.browser("browser.read", {}, {}, "crew")
        # The lock is free again and the stuck browser is gone, so the next action can start.
        async with asyncio.timeout(1):
            async with desktop.browser_lock:
                pass

    asyncio.run(run())
    assert context.closed and desktop.context is None and desktop.browser_owner is None


def test_probe_answers_unknown_instead_of_hanging_and_reports_a_saved_login(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from mavo import trial

    monkeypatch.setattr(trial, "PROBE_LIMIT", 0.2)
    closed = []

    class Desktop:
        browser_owner = None
        probes = {}

        async def browser(self, *args):
            await asyncio.sleep(30)

        async def close_owner(self, owner):
            closed.append(owner)

        def saved_session(self, host):
            return host == "portal.example.com"

    runtime = SimpleNamespace(actions=SimpleNamespace(desktop=Desktop()))
    out = asyncio.run(trial.probe_site(runtime, "https://portal.example.com"))
    assert out["unknown"] is True and out["signed_in"] is None and out["saved_session"] is True
    assert closed == ["trial"]


def test_simultaneous_sign_in_checks_share_one_look_and_later_ones_look_again():
    import asyncio
    from types import SimpleNamespace

    from mavo import trial

    looks = []

    class Desktop:
        browser_owner = None
        probes = {}

        async def browser(self, operation, args, rule, owner):
            looks.append(args["url"])
            await asyncio.sleep(0.2)
            return {"url": args["url"], "title": "Jobs", "session": "in"}

        async def close_owner(self, owner):
            pass

        def saved_session(self, host):
            return False

    runtime = SimpleNamespace(actions=SimpleNamespace(desktop=Desktop()))

    async def run():
        first = await asyncio.gather(
            *[trial.probe_site(runtime, "https://portal.example.com") for _ in range(3)]
        )
        again = await trial.probe_site(runtime, "https://portal.example.com")
        return first, again

    first, again = asyncio.run(run())
    assert all(item["signed_in"] is True for item in [*first, again])
    assert len(looks) == 2  # three simultaneous askers, one look; the next question is fresh


def test_a_saved_login_is_found_in_the_browsers_cookie_store_without_starting_it(tmp_path):
    import sqlite3

    from mavo.desktop_actions import DesktopActions

    desktop = DesktopActions(tmp_path)
    store = tmp_path / "browser" / "mavo" / "Default" / "Network"
    store.mkdir(parents=True)
    far, long_ago = int((9_999_999_999 + 11644473600) * 1e6), 1
    db = sqlite3.connect(store / "Cookies")
    db.execute(
        "create table cookies (host_key text, name text, is_persistent int, expires_utc int)"
    )
    db.executemany(
        "insert into cookies values (?,?,?,?)",
        [
            (".portal.example.com", "session_token", 1, far),
            (".news.example.com", "_ga", 1, far),
            (".old.example.com", "sid", 1, long_ago),
            (".temp.example.com", "sid", 0, 0),
        ],
    )
    db.commit()
    db.close()
    assert desktop.saved_session("app.portal.example.com") is True
    assert desktop.saved_session("news.example.com") is False  # only a tracker
    assert desktop.saved_session("old.example.com") is False  # expired
    assert desktop.saved_session("temp.example.com") is False  # gone when the window closed
    assert desktop.saved_session("elsewhere.org") is False
    assert DesktopActions(tmp_path / "none").saved_session("portal.example.com") is False


def test_a_locked_cookie_store_is_cannot_tell_not_nothing_saved(tmp_path, monkeypatch):
    """Windows will not let a running browser's cookie file be read. That once answered "no login
    saved" for a person who was signed in; it must answer None, and a running browser is asked."""
    import asyncio
    from types import SimpleNamespace

    from mavo import desktop_actions
    from mavo.desktop_actions import DesktopActions

    desktop = DesktopActions(tmp_path)
    store = tmp_path / "browser" / "mavo" / "Default" / "Network"
    store.mkdir(parents=True)
    (store / "Cookies").write_bytes(b"locked by the running browser")

    def locked(*_args):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(desktop_actions.shutil, "copyfile", locked)
    assert desktop.saved_session("portal.example.com") is None
    assert asyncio.run(desktop.saved_login("portal.example.com")) is None  # no browser to ask

    soon, long_ago = 9_999_999_999, 1_000_000_000

    async def held():
        return [
            {"domain": ".portal.example.com", "name": "session_token", "expires": soon},
            {"domain": ".news.example.com", "name": "_ga", "expires": soon},
            {"domain": "old.example.com", "name": "sid", "expires": long_ago},
            {"domain": "temp.example.com", "name": "sid", "expires": -1},
            {"domain": "evilbank.example.com", "name": "sid", "expires": soon},
        ]

    def ask(host):
        return asyncio.run(desktop.saved_login(host))

    desktop.context = SimpleNamespace(cookies=held)
    assert ask("app.portal.example.com") is True
    assert ask("news.example.com") is False  # only a tracker
    assert ask("old.example.com") is False  # expired
    assert ask("temp.example.com") is False  # session-only
    assert ask("bank.example.com") is False  # a different site that merely ends the same way
    assert ask("evilbank.example.com") is True

    async def stuck():
        raise TimeoutError

    desktop.context = SimpleNamespace(cookies=stuck)
    assert ask("portal.example.com") is None  # a browser that will not answer is not "no"


def test_a_page_says_whether_it_is_signed_in_out_or_does_not_say():
    async def hint(desktop, page):
        await desktop.sign_in_wall()
        return desktop.session_hint, desktop.login_link

    public = "<html><body><header><a href='/models'>Models</a><a href='/signin'>Sign in</a></header><h1>One API</h1></body></html>"
    assert with_page(public, hint) == ("out", "https://site.example.com/signin")
    account = "<html><body><header><a href='/m'>Models</a> <a href='/out'>Sign out</a></header><h1>Credits</h1></body></html>"
    assert with_page(account, hint)[0] == "in"
    silent = "<html><body><h1>About us</h1><p>We make things.</p></body></html>"
    assert with_page(silent, hint)[0] == "unknown"
    with_form = "<html><body><input type='password' aria-label='Password'></body></html>"
    assert with_page(with_form, hint)[0] == "out"


# AIRouter's homepage: titled "One API for Every AI Model", with a big "Get API Key" button that
# goes nowhere for a signed-in visitor; the real pages sit behind the account menu. Asked for
# "API keys", the finder clicked that button, stayed on the homepage, and still said "reached"
# (the title shares "API") five times over, until the agent ran out of steps.
CTA_SITE = {
    "/": """<html><head><title>One API for Every AI Model | AIRouter</title></head><body><header>
      <a href="/">Home</a><button onclick="void 0">Get API Key</button>
      <button id="av" aria-haspopup="menu" aria-expanded="false" onclick="
        document.getElementById('m').hidden = false; this.setAttribute('aria-expanded','true')">Account</button>
      <nav id="m" hidden><a href="/settings/keys">API Keys</a><a href="/settings/credits">Credits</a></nav>
      </header><h1>One API. Every AI Model.</h1></body></html>""",
    "/settings/keys": "<html><head><title>API Keys</title></head><body><h1>API Keys</h1></body></html>",
    "/settings/credits": "<html><head><title>Credits</title></head><body><h1>Credits</h1><p>Balance $0.68</p></body></html>",
}


def test_a_button_that_goes_nowhere_is_not_arriving_and_the_account_menu_is_tried():
    result, url, _ = navigate_site("API keys", CTA_SITE)
    assert url.endswith("/settings/keys") and result["reached"], result
    assert result["steps"][0] == {
        "clicked": "Get API Key",
        "url": "https://app.example.com/",
        "stayed": True,
    }


def test_not_found_is_said_plainly_never_reached_on_the_same_page():
    lone = {"/": CTA_SITE["/"].replace('<a href="/settings/keys">API Keys</a>', "")}
    result, url, _ = navigate_site("API keys", lone)
    assert url.endswith("/") and result["reached"] is False
    assert "Do not ask for the same name again" in result["note"] and "credits" in result["note"]
