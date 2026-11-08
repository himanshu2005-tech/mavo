import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from mavo.action_rules import RuleChange
from mavo.database import CrewInstance
from mavo.desktop_actions import host_allowed, public_host
from mavo.google_actions import ActionDraft
from tests.test_actions import action_env as action_fixture


def test_manual_browser_sign_in_uses_scoped_crew_access_and_pauses_work(client, auth):
    from mavo.database import CrewInstance

    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Browser", "provider": "ollama", "model": "test"},
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "Sign in",
            "responsibilities": "Remind at 10PM",
            "schedule_time": "22:00",
            "route_id": route["id"],
        },
    ).json()
    endpoint = f"/api/v1/crew/{crew['id']}/browser-session"
    assert (
        client.post(endpoint, headers=auth, json={"url": "https://example.com"}).status_code == 403
    )
    save(client, auth, "browser.open", "unrestricted", crew_id=crew["id"])
    runtime = client.app.state.crew_runtime
    runtime.actions.desktop.browser = AsyncMock(
        return_value={"url": "https://example.com", "title": "Sign in"}
    )
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    result = client.post(endpoint, headers=auth, json={"url": "https://example.com"})
    assert result.status_code == 200 and result.json()["opened"] and result.json()["paused"]
    runtime.actions.desktop.browser.assert_awaited_once()
    with runtime.factory() as s:
        current = s.get(CrewInstance, crew["id"])
        assert not current.is_running and runtime.actions.desktop.sign_in_held(current)
    assert client.post(endpoint, headers=auth, json={"url": "http://127.0.0.1"}).status_code == 422


@pytest.mark.asyncio
async def test_open_does_not_implicitly_grant_reading_page_content(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mavo.desktop_actions import DesktopActions

    monkeypatch.setattr("mavo.desktop_actions.public_host", AsyncMock())
    desktop = DesktopActions(tmp_path)
    desktop.browser_owner = "crew"
    desktop.context = SimpleNamespace()
    desktop.page = SimpleNamespace(
        url="https://example.com",
        is_closed=lambda: False,
        goto=AsyncMock(),
        title=AsyncMock(return_value="Example Domain"),
    )
    result = await desktop.browser(
        "browser.open",
        {"url": "https://example.com"},
        {"mode": "unrestricted", "domains": []},
        "crew",
    )
    assert result["title"] == "Example Domain" and "text" not in result and "elements" not in result


action_env = action_fixture


def test_named_browser_assignments_are_independent_and_sign_in_hold_is_bounded(client, auth):
    from types import SimpleNamespace

    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Test", "provider": "ollama", "model": "test"},
    ).json()
    crew_ids = []
    for name in ("College portal", "Personal portal"):
        crew_ids.append(
            client.post(
                "/api/v1/crew",
                headers=auth,
                json={
                    "name": name,
                    "template_id": "operator",
                    "route_id": route["id"],
                    "responsibilities": "Read the portal",
                    "trigger_type": "manual",
                },
            ).json()["id"]
        )
    save(client, auth, "browser.open", crew_id=crew_ids[0], domains=["example.com"])
    rows = client.get("/api/v1/action-policies/browser.open/crew", headers=auth).json()
    assert [(r["name"], r["authorized"]) for r in rows] == [
        ("College portal", True),
        ("Personal portal", False),
    ]
    denied = client.post(
        f"/api/v1/crew/{crew_ids[1]}/browser-session",
        headers=auth,
        json={"url": "https://example.com"},
    )
    assert denied.status_code == 403
    outside = client.post(
        f"/api/v1/crew/{crew_ids[0]}/browser-session",
        headers=auth,
        json={"url": "https://other.example"},
    )
    assert outside.status_code == 403
    desktop = client.app.state.crew_runtime.actions.desktop
    owner = SimpleNamespace(id=crew_ids[0], runtime_generation=1)
    desktop.hold_for_sign_in(owner.id, owner.runtime_generation)
    assert desktop.sign_in_held(owner)
    assert not desktop.sign_in_held(SimpleNamespace(id=crew_ids[1], runtime_generation=1))
    assert not desktop.sign_in_held(SimpleNamespace(id=owner.id, runtime_generation=2))
    desktop.interactive_until = 0
    assert not desktop.sign_in_held(owner)


def save(client, auth, operation, mode="restricted", crew_id=None, **scope):
    response = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": operation,
            "crew_id": crew_id,
            "rule": {"enabled": True, "mode": mode, **scope},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("mode", ["restricted", "unrestricted"])
def test_enabled_terminal_runs_automatically_under_access(client, auth, tmp_path, mode):
    save(
        client, auth, "terminal.run", mode, folders=[str(tmp_path)] if mode == "restricted" else []
    )
    response = client.post(
        "/api/v1/actions",
        headers=auth,
        json={
            "operation": "terminal.run",
            "arguments": {"command": "python.version", "folder": str(tmp_path)},
        },
    )
    assert response.status_code == 201, response.text
    action = response.json()
    assert action["status"] == "completed" and action["approved_by"] == "access"
    assert action["result"]["stdout"].startswith("Python 3.")
    assert client.post(f"/api/v1/actions/{action['id']}/approve", headers=auth).status_code == 410


def test_disabled_scope_escape_revision_and_pause(client, auth, tmp_path):
    payload = {
        "operation": "terminal.run",
        "arguments": {"command": "system.info", "folder": str(tmp_path)},
    }
    assert client.post("/api/v1/actions", headers=auth, json=payload).status_code == 409
    folder = tmp_path / "approved"
    folder.mkdir()
    save(client, auth, "terminal.run", folders=[str(folder)])
    assert client.post("/api/v1/actions", headers=auth, json=payload).status_code == 409
    payload["arguments"]["folder"] = str(folder)
    engine = client.app.state.crew_runtime.actions
    queued = engine.propose(ActionDraft.model_validate(payload))
    save(client, auth, "terminal.run", "unrestricted")
    with pytest.raises(RuntimeError):
        client.portal.call(engine.maybe_execute, queued["id"])
    assert client.post("/api/v1/actions/pause", headers=auth).json()["paused"]
    assert client.post("/api/v1/actions", headers=auth, json=payload).status_code == 409
    client.post("/api/v1/actions/resume", headers=auth)
    assert (
        client.post("/api/v1/actions", headers=auth, json=payload).json()["status"] == "completed"
    )


def test_send_limits_run_without_prompts_and_unrestricted_removes_recipient_limit(action_env):
    client, auth, _, state = action_env
    save(client, auth, "gmail.send", recipients=["friend@example.com"])
    payload = {
        "operation": "gmail.send",
        "arguments": {"to": "other@example.com", "subject": "Hi", "body": "Test"},
    }
    assert client.post("/api/v1/actions", headers=auth, json=payload).status_code == 409
    assert not state["writes"]
    payload["arguments"]["to"] = "friend@example.com"
    assert (
        client.post("/api/v1/actions", headers=auth, json=payload).json()["status"] == "completed"
    )
    save(client, auth, "gmail.send", "unrestricted")
    payload["arguments"]["to"] = "other@example.com"
    assert (
        client.post("/api/v1/actions", headers=auth, json=payload).json()["status"] == "completed"
    )
    assert len(state["writes"]) == 2


def test_crew_access_is_not_inherited_from_workspace(client, auth, tmp_path):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Local", "provider": "ollama", "model": "test"},
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Git helper",
            "template_id": "coding",
            "route_id": route["id"],
            "responsibilities": "Check Git",
            "trigger_type": "manual",
        },
    ).json()
    save(client, auth, "terminal.run", "unrestricted")
    engine = client.app.state.crew_runtime.actions
    draft = ActionDraft(
        operation="terminal.run", arguments={"command": "python.version", "folder": str(tmp_path)}
    )
    with pytest.raises(RuntimeError):
        engine.propose(draft, crew["id"], crew.get("runtime_generation", 0))
    save(client, auth, "terminal.run", crew_id=crew["id"], folders=[str(tmp_path)])
    with engine.factory() as session:
        row = session.get(CrewInstance, crew["id"])
        generation = row.runtime_generation
    queued = engine.propose(draft, crew["id"], generation)
    assert client.portal.call(engine.maybe_execute, queued["id"])["status"] == "completed"


def test_terminal_and_browser_reject_code_and_private_urls():
    for args in (
        {"command": "powershell", "folder": "D:\\test"},
        {"command": "git.status", "folder": "D:\\test", "shell": True},
    ):
        with pytest.raises(ValidationError):
            ActionDraft(operation="terminal.run", arguments=args)
    for url in (
        "file:///C:/secret",
        "http://localhost",
        "https://user:secret@example.com",
        "javascript:alert(1)",
    ):
        with pytest.raises(ValidationError):
            ActionDraft(operation="browser.open", arguments={"url": url})
    assert not host_allowed("https://example.com.evil.test", ["example.com"])
    for host in ("localhost", "127.0.0.1", "*.example.com", "example.com/path"):
        with pytest.raises(ValidationError):
            RuleChange(operation="browser.open", rule={"enabled": True, "domains": [host]})
    for app in ("powershell.exe", "windowsterminal.exe", "msedge.exe"):
        with pytest.raises(ValidationError):
            RuleChange(operation="windows.type", rule={"enabled": True, "apps": [app]})


def fake_processes(monkeypatch, tmp_path, processes):
    """Pretend these processes exist: [(pid, [arguments...])]."""
    from types import SimpleNamespace

    import psutil

    monkeypatch.setattr(
        psutil,
        "process_iter",
        lambda _attrs: [SimpleNamespace(info={"pid": p, "cmdline": a}) for p, a in processes],
    )


def test_the_automated_browser_is_not_mistaken_for_the_sign_in_window(tmp_path, monkeypatch):
    """Both use Mavo's profile. A leftover automated browser once made every run say "please sign
    in" until it was gone, because any process on the profile counted as the sign-in window."""
    from mavo.desktop_actions import DesktopActions

    desktop = DesktopActions(tmp_path)
    profile = desktop.profile_dir()
    data = "--user-data-dir=" + profile
    fake_processes(
        monkeypatch,
        tmp_path,
        [
            (10, ["msedge.exe", data, "--remote-debugging-pipe"]),  # a Crew's browser
            (11, ["msedge.exe", data, "--type=renderer"]),  # a helper, never counted
            (20, ["msedge.exe", data, "--remote-debugging-port=51234", "--window-size=1280,720"]),
            (30, ["msedge.exe", "--user-data-dir=C:/someone/elses/profile"]),  # not Mavo's
        ],
    )
    assert desktop.automated_pids() == [10]
    assert desktop.user_window_pids() == [20] and desktop.user_window_open()
    fake_processes(monkeypatch, tmp_path, [(10, ["msedge.exe", data, "--remote-debugging-pipe"])])
    assert desktop.automated_pids() == [10]
    assert desktop.user_window_pids() == [] and not desktop.user_window_open()


def test_a_leftover_automated_browser_is_cleared_before_the_next_one_starts(tmp_path, monkeypatch):
    import subprocess

    from mavo.desktop_actions import DesktopActions

    desktop = DesktopActions(tmp_path)
    killed = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_: killed.append(cmd[2]))
    state = {"left": ["7"]}

    def leave_when_killed():
        return [int(p) for p in state["left"]]

    monkeypatch.setattr(desktop, "automated_pids", leave_when_killed)
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **_: (killed.append(cmd[2]), state["left"].clear())
    )
    asyncio.run(desktop.clear_leftovers(patience=0))
    assert killed == ["7"] and desktop.automated_pids() == []

    # Nothing left: nothing is killed and nothing waits.
    killed.clear()
    asyncio.run(desktop.clear_leftovers())
    assert killed == []

    # One that leaves by itself during the grace period is left alone.
    polls = iter([[5], [5], []])
    monkeypatch.setattr(desktop, "automated_pids", lambda: next(polls, []))
    asyncio.run(desktop.clear_leftovers(patience=3))
    assert killed == []


def test_closing_mavo_is_never_held_up_by_a_browser_that_will_not_close(tmp_path, monkeypatch):
    """A browser that is already gone or frozen never answers `close`; and a lock something still
    holds must not hold shutdown either. Closing waits briefly, ends it outright and returns."""
    import subprocess

    from mavo.desktop_actions import DesktopActions

    class Frozen:
        async def close(self):
            await asyncio.Event().wait()

        async def stop(self):
            await asyncio.Event().wait()

    async def scenario():
        desktop = DesktopActions(tmp_path)
        desktop.close_patience = 0.05
        ended = []
        monkeypatch.setattr(desktop, "automated_pids", lambda: [])
        monkeypatch.setattr(subprocess, "run", lambda cmd, **_: ended.append(cmd))
        desktop.context, desktop.playwright, desktop.page = Frozen(), Frozen(), object()
        desktop.browser_owner = "crew"
        await desktop.browser_lock.acquire()  # an action that never finishes still holds it
        await asyncio.wait_for(desktop.close(), timeout=5)
        assert desktop.context is None and desktop.playwright is None
        assert desktop.browser_owner is None
        assert desktop.browser_lock.locked()  # not ours to release

    asyncio.run(scenario())


def test_a_site_and_its_www_twin_are_one_site_but_nothing_else_is():
    """airouter.in redirects to www.airouter.in: a Crew allowed one must be able to read the page
    it was just sent to, in either direction, and look-alikes stay refused."""
    assert host_allowed("https://www.airouter.in/", ["airouter.in"])
    assert host_allowed("https://airouter.in/billing", ["www.airouter.in"])
    assert host_allowed("https://airouter.in/", ["airouter.in"])
    assert host_allowed("https://www.airouter.in/", ["www.airouter.in", "other.example"])
    for refused in (
        "https://app.airouter.in/",  # another subdomain is another page of the world
        "https://www.app.airouter.in/",
        "https://wwwairouter.in/",
        "https://www.airouter.in.evil.test/",
        "https://evil-airouter.in/",
        "http://www.airouter.in/",
        "https://www.airouter.in:8443/",
        "https://user@www.airouter.in/",
    ):
        assert not host_allowed(refused, ["airouter.in"]), refused
    assert not host_allowed("https://www.example.com/", [])


@pytest.mark.asyncio
async def test_private_dns_is_rejected(monkeypatch):
    async def private(*args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", private)
    with pytest.raises(ValueError):
        await public_host("example.com")


def test_disabled_access_can_be_saved_after_folder_was_deleted(client, auth, tmp_path):
    folder = tmp_path / "gone"
    folder.mkdir()
    save(client, auth, "terminal.run", folders=[str(folder)])
    folder.rmdir()
    response = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={"operation": "terminal.run", "rule": {"enabled": False, "folders": [str(folder)]}},
    )
    assert response.status_code == 200


def test_bounded_automatic_followups(client, auth, tmp_path):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Bounded", "provider": "ollama", "model": "test"},
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Versions",
            "template_id": "coding",
            "route_id": route["id"],
            "responsibilities": "Check versions",
            "trigger_type": "manual",
            "max_model_calls": 2,
            "daily_call_limit": 5,
        },
    ).json()
    save(client, auth, "terminal.run", "unrestricted", crew_id=crew["id"])
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    runtime = client.app.state.crew_runtime
    runtime.router.chat = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "state": "continue",
                    "summary": "Read result",
                    "next_step": "Next version",
                    "actions": [
                        {
                            "operation": "terminal.run",
                            "arguments": {"command": command, "folder": str(tmp_path)},
                        }
                    ],
                }
            )
            for command in ("python.version", "system.info")
        ]
    )
    client.portal.call(runtime.work, crew["id"])
    client.portal.call(runtime.work, crew["id"])
    result = client.get("/api/v1/crew", headers=auth).json()[0]
    assert result["runtime_state"] == "needs_input" and result["next_run_at"] is None
    assert result["daily_calls_used"] == 2
    client.portal.call(runtime.tick)
    assert runtime.router.chat.await_count == 2


def test_cancelled_action_is_unknown_and_cannot_replay(action_env):
    client, auth, engine, _ = action_env
    queued = engine.propose(
        ActionDraft(operation="tasks.create", arguments={"title": "No external write"})
    )

    async def scenario():
        entered = asyncio.Event()

        async def blocked(*args):
            entered.set()
            await asyncio.Event().wait()

        engine.perform = blocked
        task = asyncio.create_task(engine.execute(queued["id"]))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    client.portal.call(scenario)
    row = client.get("/api/v1/actions", headers=auth).json()[0]
    assert row["status"] == "unknown"
    assert client.portal.call(engine.maybe_execute, row["id"])["status"] == "unknown"


def test_a_window_stuck_beyond_the_screen_edge_is_brought_back_and_a_good_one_is_left_alone():
    from mavo.desktop_actions import default_window, fit_on_screen

    area = (0, 0, 1536, 816)
    assert default_window(area) == (60, 30, 1280, 746)
    # The off-screen spot of the background browser (only 30 px showing) is not reachable.
    assert fit_on_screen((-1337, 0, 30, 882), area) == (60, 30, 1280, 746)
    assert fit_on_screen((-32000, -32000, -30700, -31200), area) == (60, 30, 1280, 746)
    assert fit_on_screen((2400, 100, 3700, 900), area) == (60, 30, 1280, 746)  # on another screen
    # A window that is on the screen, even partly under the edge, stays where the person put it.
    assert fit_on_screen((120, 40, 1400, 800), area) is None
    assert fit_on_screen((300, 100, 1700, 700), area) is None
    # A small screen still gets a window that fits it.
    assert default_window((0, 0, 1024, 700)) == (60, 30, 904, 630)


def test_a_site_that_signs_in_elsewhere_is_allowed_together_with_its_sign_in_hosts():
    """Microsoft Forms answers forms.office.com with a redirect to login.microsoftonline.com and
    lands on forms.cloud.microsoft; a Crew allowed only the first was stopped at the redirect."""
    from mavo.desktop_actions import host_allowed, with_companions

    domains = with_companions(["forms.office.com"])
    for url in (
        "https://forms.office.com/Pages/DesignPage.aspx",
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?x=1",
        "https://login.live.com/",
        "https://forms.cloud.microsoft/landing",
    ):
        assert host_allowed(url, domains), url
    # Look-alikes, other Microsoft hosts, ports and plain http are still outside it.
    for url in (
        "https://login.microsoftonline.com.evil.example/",
        "https://evil-login.microsoftonline.com/",
        "https://outlook.office.com/mail",
        "https://login.microsoftonline.com:8443/",
        "http://login.microsoftonline.com/",
    ):
        assert not host_allowed(url, domains), url
    # Sites that have no such partners are untouched, and the caller's list is not modified.
    assert with_companions(["example.com", "www.example.com"]) == ["example.com", "www.example.com"]
    given = ["outlook.office.com"]
    assert "login.microsoftonline.com" in with_companions(given) and given == ["outlook.office.com"]


def test_a_refused_redirect_names_the_site_it_was_sent_to(tmp_path):
    from mavo.desktop_actions import DesktopActions

    desktop = DesktopActions(tmp_path)
    assert desktop.blocked_hosts == []
    desktop.blocked_hosts = ["login.example-sso.com", "login.example-sso.com", "id.example-sso.com"]
    message = desktop.blocked_message()
    assert (
        "login.example-sso.com, id.example-sso.com" in message and "not allowed to open" in message
    )
