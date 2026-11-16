"""A keyboard shortcut that runs an agent ("when I press Win+F9, check my credits"): read the way
the person writes it, set when the agent is made or in its settings, never shared by two agents,
and a press runs it at once as Check now does."""

from mavo.database import CrewInstance
from mavo.hotkeys import normalize, parse, run_now, wanted
from mavo.schemas import CrewDraft


def test_shortcuts_are_read_the_way_people_write_them():
    assert normalize("win + f9") == "Win+F9"
    assert normalize("Windows+F9") == "Win+F9"
    assert normalize("alt+ctrl+r") == "Ctrl+Alt+R"  # one order, however typed
    assert normalize("Ctrl+Shift+Space") == "Ctrl+Shift+Space"
    assert normalize("F13") == "F13"  # a key no one types with
    assert parse("Win+F9")[1:] == (0x0008, 0x78)
    for bad in ("F9", "r", "Ctrl", "Ctrl+Ctrl+R", "Ctrl+Banana", "", None):
        assert normalize(bad) is None  # a plain key would fire while typing


def test_mavo_core_can_give_a_new_agent_its_shortcut_and_a_bad_one_never_refuses_the_draft():
    base = {"template_id": "research", "name": "Credits", "responsibilities": "Check credits."}
    assert CrewDraft.model_validate({**base, "hotkey": "win+f9"}).hotkey == "Win+F9"
    assert CrewDraft.model_validate({**base, "hotkey": "the blue button"}).hotkey is None
    assert CrewDraft.model_validate(base).hotkey is None


def add(runtime, name, **plan):
    with runtime.factory() as session:
        crew = CrewInstance(
            name=name,
            template_id="research",
            status="active",
            is_running=True,
            provider="ollama",
            model="m",
            permissions=["model:invoke"],
            trigger_type="manual",
            responsibilities="Check credits.",
            configuration_plan=plan,
        )
        session.add(crew)
        session.commit()
        return crew.id


def test_each_running_agent_gets_its_own_shortcut_and_a_press_runs_it(client, auth):
    from tests.test_tasks import setup

    runtime = setup(client, auth)
    first = add(runtime, "Credits", draft={"hotkey": "Win+F9"})
    add(runtime, "Second", hotkey="Win+F9")  # the same keys: the first keeps them
    third = add(runtime, "Third", hotkey="Ctrl+Alt+R")
    with runtime.factory() as session:
        shortcuts = wanted(session)
        assert {k: v[0] for k, v in shortcuts.items()} == {first: "Win+F9", third: "Ctrl+Alt+R"}
        session.get(CrewInstance, third).is_running = False  # stopped: its shortcut is let go
        session.commit()
        assert third not in wanted(session)
    assert run_now(runtime.factory, first)
    with runtime.factory() as session:
        crew = session.get(CrewInstance, first)
        assert crew.runtime_state == "waiting" and crew.next_run_at is not None
        crew.runtime_state = "working"
        session.commit()
    assert not run_now(runtime.factory, first)  # already working: a second press does nothing


def test_the_person_sets_changes_and_clears_a_shortcut(client, auth):
    from tests.test_tasks import setup

    runtime = setup(client, auth)
    crew_id = add(runtime, "Credits", draft={"hotkey": "Win+F9"})
    other = add(runtime, "Other")
    url = f"/api/v1/crew/{crew_id}/hotkey"
    assert (
        client.post(url, json={"hotkey": "ctrl+alt+c"}, headers=auth).json()["hotkey"]
        == "Ctrl+Alt+C"
    )
    bad = client.post(url, json={"hotkey": "c"}, headers=auth)
    assert bad.status_code == 422 and "Win+F9" in bad.text
    taken = client.post(f"/api/v1/crew/{other}/hotkey", json={"hotkey": "Ctrl+Alt+C"}, headers=auth)
    assert taken.status_code == 409 and "Credits" in taken.text
    cleared = client.post(url, json={"hotkey": None}, headers=auth).json()
    assert cleared["hotkey"] is None  # cleared stays cleared, not back to the one it was made with
