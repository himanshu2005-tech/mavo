# ruff: noqa: E501, F811
"""Building with a team (Architect, Coder, Tester, Reviewer, Debugger), and scheduled Crews that
are given only what is new."""

import asyncio

import pytest

from mavo.projects import Projects
from mavo.site_skills import SkillStop
from mavo.task_plan import TaskPlan
from mavo.team_build import ProjectParams, app_check, check_web, project_save, team_plan
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import checkpoint, run_work
from tests.test_tasks import alerts, setup, start, wait

GOOD = """<!doctype html><html><body><input id=t aria-label=Task><button id=b>Add</button><ul id=l></ul>
<script>
const items = JSON.parse(localStorage.getItem('i') || '[]');
const draw = () => { l.innerHTML = ''; items.forEach(x => { const li = document.createElement('li'); li.textContent = x; l.appendChild(li); }); };
const add = () => { if (!t.value.trim()) return; items.push(t.value); localStorage.setItem('i', JSON.stringify(items)); t.value=''; draw(); };
b.onclick = add; t.onkeydown = e => { if (e.key === 'Enter') add(); }; draw();
</script></body></html>"""
BROKEN = "<html><body><input><button>Add</button><ul></ul><script>undefinedFunction()</script></body></html>"
TIMER = """<html><body><h1 id=t>25:00</h1><input type=number value=25 aria-label=Minutes><button id=s>Start</button><button id=r>Reset</button>
<script>let n = 1500; s.onclick = () => { n--; t.textContent = Math.floor(n/60) + ':' + String(n%60).padStart(2,'0'); }; r.onclick = () => { n = 1500; t.textContent = '25:00'; };</script></body></html>"""
CRASHES_ON_START = TIMER.replace("n--;", "n--; missing.call();")


def test_build_me_a_thing_is_the_whole_team_whatever_else_the_plan_held():
    plan = TaskPlan.model_validate(
        {
            "title": "x",
            "deliver": "y",
            "steps": [
                {"id": "pre", "kind": "helper", "title": "Plan", "instructions": "Plan it"},
                {
                    "id": "b",
                    "kind": "skill",
                    "skill": "team.build",
                    "params": {"request": "a todo list app", "name": "Todo List"},
                },
            ],
        }
    )
    # One live step: the lead picks the team when it runs, so no roles are fixed here.
    assert [s.skill for s in plan.steps] == ["team.build"]
    assert plan.final == "build" and plan.title == "Build: Todo List"
    assert plan.steps[0].params["project"] == "todo-list"
    # Read back from the database it stays the same plan (no second expansion).
    again = TaskPlan.model_validate(plan.model_dump(mode="json"))
    assert again.steps[0].params == plan.steps[0].params


def test_how_much_care_comes_from_the_persons_own_words():
    from mavo.team_build import rounds_for

    assert rounds_for("a quick todo list") == 1
    assert rounds_for("a todo list") == 3
    assert rounds_for("a polished expense tracker") == 5
    quick = TaskPlan.model_validate(team_plan("a quick todo list", "Todo"))
    assert quick.model_calls < TaskPlan.model_validate(team_plan("a todo list", "Todo")).model_calls


def test_the_lead_names_the_team_and_a_lead_that_does_not_gets_the_usual_one():
    from mavo.team_build import design_of, read_team

    lead = (
        "GOAL: an expense tracker. KIND: web.\nTEAM:\n- builder | Developer | writes the app\n"
        "- builder | UI Designer | polishes the look\n- reviewer | Security Reviewer | input "
        "handling and stored data\n- fixer | Debugger | fixes what was found"
    )
    builders, reviewers, fixer = read_team(lead)
    assert [m.role for m in builders] == ["Developer", "UI Designer"]
    assert [m.role for m in reviewers] == ["Security Reviewer"] and fixer.role == "Debugger"
    assert "UI Designer" not in design_of(lead) and "GOAL: an expense tracker" in design_of(lead)
    builders, reviewers, fixer = read_team("GOAL: x")
    assert [m.role for m in builders + reviewers + [fixer]] == ["Developer", "Reviewer", "Debugger"]
    # A real lead wrote its team, then "Updated." and the whole team again: the last one counts.
    twice = "TEAM:\n- builder | Developer | a\n- fixer | Old Fixer | b\nUpdated.\n" + lead
    builders, reviewers, fixer = read_team(twice)
    assert [m.role for m in builders] == ["Developer", "UI Designer"] and fixer.role == "Debugger"
    # A tiny script: the lead may decide it needs no reviewer at all.
    _, reviewers, _ = read_team("- builder | Developer | all of it\n- fixer | Developer | fixes")
    assert reviewers == []


class Desk:
    def __init__(self, root):
        self.projects = Projects(default_root=root)


def save(desk, text):
    return asyncio.run(
        project_save(desk, {}, "o", ProjectParams(project="todo-list"), inputs={"code": text})
    )


def test_the_coders_files_are_saved_and_no_changes_keeps_them(tmp_path):
    desk = Desk(tmp_path)
    out = save(desk, "```file:index.html\n" + GOOD + "\n```\nNOTES: a todo list.")
    assert "index.html" in out and "NOTES: a todo list." in out
    assert (
        (tmp_path / "todo-list" / "index.html")
        .read_text(encoding="utf-8")
        .startswith("<!doctype html>")
    )
    assert "No changes needed" in save(desk, "NO CHANGES")
    with pytest.raises(SkillStop, match="cut off before its end"):  # the reply ran out
        save(
            Desk(tmp_path / "cut"), "```file:index.html\n<!doctype html><html><body><script>let x ="
        )
    with pytest.raises(SkillStop):  # nothing was ever written: that is not a build
        asyncio.run(
            project_save(
                Desk(tmp_path / "empty"),
                {},
                "o",
                ProjectParams(project="x"),
                inputs={"code": "I could not."},
            )
        )


def test_a_fixer_sends_small_edits_not_the_whole_file_again(tmp_path):
    """A production-sized news app: the fixer rewrote the whole page and ran out of room."""
    from mavo.team_build import apply_edit

    assert apply_edit("a\n  b\nc", "b", "B") == "a\n  B\nc"
    assert (
        apply_edit("<ul>\n    <li>x</li>\n</ul>", "<ul>\n<li>x</li>", "<ol>\n<li>x</li>")
        == "<ol>\n<li>x</li>\n</ul>"
    )
    assert apply_edit("abc", "zzz", "y") is None
    desk = Desk(tmp_path)
    save(desk, "```file:index.html\n" + GOOD + "\n```")
    out = save(
        desk,
        "EDIT index.html\n<<<<<<< FIND\nconst add = () => {\n=======\nconst add = () => { console.log('add');\n>>>>>>> REPLACE\n"
        "EDIT index.html\n<<<<<<< FIND\nnot in the file\n=======\nx\n>>>>>>> REPLACE\nCHANGES: logs adds.",
    )
    page = (tmp_path / "todo-list" / "index.html").read_text(encoding="utf-8")
    assert "console.log('add')" in page and "draw();" in page  # the rest of the page is kept
    assert (
        "1 change to index.html" in out
        and "Could not apply 1: a FIND text is not in index.html" in out
    )
    assert "CHANGES: logs adds." in out
    # A reply cut off in its last edit: the whole edits are made, and it says so.
    out = save(
        desk,
        "EDIT index.html\n<<<<<<< FIND\nconsole.log('add');\n=======\n\n>>>>>>> REPLACE\nEDIT index.html\n<<<<<<< FIND\nconst dr",
    )
    assert "1 change" in out and "cut off" in out
    assert "console.log" not in (tmp_path / "todo-list" / "index.html").read_text(encoding="utf-8")


def browser_or_skip():
    from mavo.desktop_actions import installed_browsers

    if not installed_browsers():
        pytest.skip("No Chromium browser installed")


def test_the_tester_tries_the_app_like_a_person_and_catches_script_errors(tmp_path):
    browser_or_skip()
    page = tmp_path / "index.html"
    page.write_text(GOOD, encoding="utf-8")
    good = asyncio.run(check_web(page))
    assert good == [
        "The page opened and shows content.",
        "Typing an item and pressing Enter (or Add) put it on the page.",
        "After a reload the item is still there.",
        "Pressed each of its 1 buttons once.",
    ]
    # Not a list app (a timer): no "add an item" verdict, but every button is pressed.
    page.write_text(TIMER, encoding="utf-8")
    assert asyncio.run(check_web(page)) == [
        "The page opened and shows content.",
        "Pressed each of its 2 buttons once.",
    ]
    page.write_text(CRASHES_ON_START, encoding="utf-8")
    assert asyncio.run(check_web(page))[0].startswith("Script error: missing is not defined")
    page.write_text(BROKEN, encoding="utf-8")
    bad = asyncio.run(check_web(page))
    assert bad[0].startswith(
        "Script error: undefinedFunction is not defined ("
    )  # once per load, merged
    assert "did NOT put it on the page" in " ".join(bad)


def test_the_test_report_says_where_it_is_and_whether_it_passed(tmp_path):
    browser_or_skip()
    desk = Desk(tmp_path)
    save(desk, "```file:index.html\n" + GOOD + "\n```")
    report = asyncio.run(app_check(desk, {}, "o", ProjectParams(project="todo-list")))
    assert report.splitlines()[0].startswith(f"Built in {tmp_path / 'todo-list'}. Open ")
    assert "TEST: passed." in report
    save(desk, "```file:index.html\n" + BROKEN + "\n```")
    assert "TEST: problems found." in asyncio.run(
        app_check(desk, {}, "o", ProjectParams(project="todo-list"))
    )


EXPENSES = """<!doctype html><html><body><form id=f><input id=d aria-label=Description required>
<input id=a type=number aria-label=Amount required><select id=c><option value="">Pick</option><option value=food>Food</option></select>
<button>Add expense</button></form><ul id=l></ul><script>
f.onsubmit = e => { e.preventDefault(); if (!d.value || !a.value || !c.value) return; const li = document.createElement('li'); li.textContent = d.value + ' ' + a.value + ' ' + c.value; l.appendChild(li); localStorage.setItem('x', l.innerHTML); };
l.innerHTML = localStorage.getItem('x') || '';</script></body></html>"""


def test_the_tester_fills_the_whole_form_like_a_person():
    """An expense tracker was failed: only the first box was filled, so it refused to add."""
    browser_or_skip()
    import tempfile
    from pathlib import Path

    page = Path(tempfile.mkdtemp()) / "index.html"
    page.write_text(EXPENSES, encoding="utf-8")
    report = asyncio.run(check_web(page))
    assert "Typing an item and pressing Enter (or Add) put it on the page." in report, report
    assert "After a reload the item is still there." in report
    # A real React build: the amount is a plain text box (placeholder "25.50") that refuses words.
    text_amount = EXPENSES.replace(
        "<input id=a type=number aria-label=Amount required>",
        "<input id=a type=text placeholder=25.50>",
    ).replace("!a.value", "!(Number(a.value) > 0)")
    page.write_text(text_amount, encoding="utf-8")
    report = asyncio.run(check_web(page))
    assert "Typing an item and pressing Enter (or Add) put it on the page." in report, report


NEWS = """<!doctype html><html><body><input id=u aria-label="Feed URL" placeholder="https://example.com/feed.xml"><button id=b>Add feed</button><ul id=l></ul><p id=s></p>
<script>
const feeds = JSON.parse(localStorage.getItem('f') || '["https://feeds.bbci.co.uk/news/rss.xml"]');
const draw = () => { l.innerHTML = ''; feeds.forEach(x => { const li = document.createElement('li'); li.textContent = x; l.appendChild(li); }); };
const load = () => feeds.forEach(x => fetch(x).catch(() => { s.textContent = 'Could not load some feeds.'; }));
b.onclick = () => { if (!/^https?:\\/\\//.test(u.value)) { s.textContent = 'Enter a valid URL'; return; } feeds.push(u.value); localStorage.setItem('f', JSON.stringify(feeds)); draw(); load(); };
draw(); load();
</script></body></html>"""


def test_a_news_app_is_given_a_feed_address_and_its_held_back_fetches_are_not_its_errors(tmp_path):
    """A real news app was failed for the test's own blocked requests and for a word typed into its
    feed URL box."""
    browser_or_skip()
    page = tmp_path / "index.html"
    page.write_text(NEWS, encoding="utf-8")
    report = asyncio.run(check_web(page))
    assert not any("error" in line.lower() for line in report), report
    assert "Typing an item and pressing Enter (or Add) put it on the page." in report
    assert "After a reload the item is still there." in report
    held = next(line for line in report if line.startswith("It asked the internet"))
    assert "feeds.bbci.co.uk" in held and "example.com" in held


def test_a_whole_page_without_its_label_is_saved_and_unusable_code_is_said(tmp_path):
    desk = Desk(tmp_path)
    save(
        desk,
        "file:index.html\n```html\n"
        + GOOD.replace("<!doctype html>", "<!DOCTYPE html>")
        + "\n```\nCHANGES: all.",
    )
    assert "localStorage" in (tmp_path / "todo-list" / "index.html").read_text(encoding="utf-8")
    with pytest.raises(SkillStop, match="not as EDIT blocks or labelled files"):
        save(desk, "Change this:\n```javascript\nconst add = () => {};\n```")
    with pytest.raises(SkillStop, match="described changes but sent no EDIT blocks"):
        save(desk, "CHANGES: refined the invalid bill handling.")
    assert "No changes needed" in save(desk, "All good. NO CHANGES")


def test_asked_for_react_the_team_is_told_and_the_tester_checks(tmp_path):
    plan = TaskPlan.model_validate(
        team_plan("Create a basic react app for my expenses", "Expenses")
    )
    build = plan.steps[0]
    assert build.params["request"].count("REQUIRED: the person asked for React") == 1
    assert build.params["project"] == "expenses" and build.params["framework"] == "react"
    plain = TaskPlan.model_validate(team_plan("a todo list", "Todo"))
    assert plain.steps[0].params.get("framework") is None
    browser_or_skip()
    desk = Desk(tmp_path)
    asyncio.run(
        project_save(
            desk,
            {},
            "o",
            ProjectParams(project="expenses"),
            inputs={"code": "```file:index.html\n" + GOOD + "\n```"},
        )
    )
    report = asyncio.run(
        app_check(desk, {}, "o", ProjectParams(project="expenses", framework="react"))
    )
    assert "You asked for React, but the app does NOT use it." in report
    assert "TEST: problems found." in report


def test_another_react_or_babel_of_the_same_major_is_tested_with_the_pinned_build(tmp_path):
    """A real build loaded Babel 7.24.7: blocked, blank in the test, five rounds lost."""
    from mavo.team_build import pinned_build

    assert pinned_build("https://unpkg.com/@babel/standalone@7.24.7/babel.min.js") == (
        "https://unpkg.com/@babel/standalone@7.26.4/babel.min.js"
    )
    assert pinned_build("https://cdn.jsdelivr.net/npm/react@18.2.0/umd/react.development.js") == (
        "https://unpkg.com/react@18.3.1/umd/react.development.js"
    )
    assert pinned_build("https://unpkg.com/react@17.0.2/umd/react.production.min.js") is None
    assert pinned_build("https://unpkg.com/lodash@4.17.21/lodash.min.js") is None
    # Any other script is named in the report, so the fixer knows exactly what to change.
    browser_or_skip()
    page = tmp_path / "index.html"
    page.write_text(
        '<html><body><script src="https://cdn.example.com/chart.js"></script><p>Hi</p></body></html>',
        encoding="utf-8",
    )
    report = asyncio.run(check_web(page))
    assert report[0].startswith("Its script https://cdn.example.com/chart.js could NOT load")
    assert not any(line.startswith("Console error") for line in report), report


def test_a_review_is_summed_up_by_its_first_numbered_problem_not_its_musing():
    from mavo.team_build import first_point

    review = (
        "UI/UX issues. Let me think about the invalid state from the design.\n\n"
        "1. **The amount field** accepts negative numbers: add min=0 and a check.\n"
        "2. Another.\nVERDICT: fix"
    )
    assert first_point(review) == "The amount field accepts negative numbers: add min=0 and a check"


def test_what_is_left_is_said_plainly_and_a_minor_point_does_not_hold_the_build():
    """A real question: "Code Reviewer: Minor: Pausing during the final second (remaining < 1s)
    leaves the timer frozen ... Fix: in `pauseTimer`, if remainingMs is small (e.g.,
    `Math.ceil(remain." Long, technical, cut mid-word, and about a minor point."""
    from mavo.team_build import first_point, plain, wants_fix

    minor = (
        "1. [minor] Pausing in the last second freezes the timer — fix: in `pauseTimer`, if "
        "remainingMs < 1000 call finish().\nVERDICT: fix"
    )
    assert not wants_fix(minor)  # minor alone: no round, no question
    major = minor.replace("1. [minor]", "1. [minor] Long titles overflow — fix: wrap.\n2. [major]")
    assert wants_fix(major)
    assert first_point(major) == "Pausing in the last second freezes the timer"
    assert wants_fix("1. It crashes on start.\nVERDICT: fix")  # untagged still counts
    long = "Minor: Pausing during the final second (remaining < 1s) leaves the timer frozen at 00:01 in paused state and it never finishes. Fix: in `pauseTimer`, if remainingMs is small (e.g., `Math.ceil(remain"
    shown = plain(long, 70)
    assert "Fix" not in shown and "`" not in shown and not shown.startswith("Minor")
    assert len(shown) <= 71 and shown.endswith("…") and shown[-2] != " "


def test_only_the_pinned_react_and_vue_builds_may_load_during_a_test():
    from mavo.team_build import allowed_in_test

    assert allowed_in_test("file:///C:/x/index.html")
    assert allowed_in_test("https://unpkg.com/react@18.3.1/umd/react.production.min.js")
    assert allowed_in_test("https://unpkg.com/@babel/standalone@7.26.4/babel.min.js")
    for other in (
        "https://unpkg.com/react@19.0.0/x.js",
        "https://evil.example/react@18.3.1/",
        "https://unpkg.com/lodash@4/x.js",
        "http://unpkg.com/react@18.3.1/x",
    ):
        assert not allowed_in_test(other), other


def test_a_python_build_is_compiled_never_run(tmp_path):
    desk = Desk(tmp_path)
    asyncio.run(
        project_save(
            desk,
            {},
            "o",
            ProjectParams(project="tool"),
            inputs={"code": "```file:main.py\nprint('hi'\n```"},
        )
    )
    report = asyncio.run(app_check(desk, {}, "o", ProjectParams(project="tool")))
    assert "main.py: SYNTAX ERROR line" in report and "TEST: problems found." in report


def allow_projects(client, auth, runtime, root):
    runtime.actions.desktop.projects.default_root = root
    for op in ("project.create", "project.write", "project.read"):
        client.put(
            "/api/v1/action-policies",
            headers=auth,
            json={
                "operation": op,
                "rule": {"enabled": True, "mode": "unrestricted", "domains": []},
            },
        ).raise_for_status()


def file_block(html: str) -> str:
    return "```file:index.html\n" + html + "\n```"


def team_chat(runtime, lead, builders=(), reviews=(), fixes=()):
    """A model that answers each member in turn; records (system, prompt) of every call."""
    seen, builders, reviews, fixes = [], list(builders), list(reviews), list(fixes)

    async def chat(provider, model, messages, system, credential_id=None):
        prompt = messages[0]["content"]
        seen.append((system, prompt))
        if "tech lead" in system:
            return lead(prompt) if callable(lead) else lead
        if "Your part:" in system:
            return builders.pop(0) if builders else "NO CHANGES"
        if "Your focus:" in system:
            return reviews.pop(0) if reviews else "VERDICT: pass"
        return fixes.pop(0) if fixes else "NO CHANGES"

    runtime.router.chat = chat
    return seen


def asked(client, auth, run_id):
    import time

    for _ in range(600):
        run = client.get(f"/api/v1/tasks/{run_id}", headers=auth).json()
        step = next((s for s in run["steps"] if s.get("ask")), None)
        if step or run["status"] != "running":
            return step
        time.sleep(0.1)
    return None


TEAM = (
    "<thinking>\n1. Goal: a todo list that keeps its items.\n2. Approach: a developer, then a "
    "designer; security matters for stored data.\n</thinking>\n"
    "GOAL: a todo list. KIND: web. FILES: index.html.\nTEAM:\n"
    "- builder | Developer | writes the app\n- builder | UI Designer | polishes the look\n"
    "- reviewer | Security Reviewer | stored data and input handling\n"
    "- fixer | Debugger | fixes what was found"
)


def test_the_team_the_lead_picked_builds_tests_reviews_and_fixes_until_it_works(
    client, auth, tmp_path
):
    browser_or_skip()
    runtime = setup(client, auth)
    allow_projects(client, auth, runtime, tmp_path)
    seen = team_chat(
        runtime,
        TEAM,
        builders=[file_block(BROKEN) + "\nNOTES: first try.", "NO CHANGES"],
        reviews=["1. undefinedFunction is not defined: remove it. VERDICT: fix", "VERDICT: pass"],
        fixes=[file_block(GOOD) + "\nCHANGES: removed the broken call."],
    )
    done = wait(
        client,
        auth,
        start(client, auth, team_plan("Build me a todo list app", "Todo List")),
        timeout=240,
    )
    assert done["status"] == "done", done
    assert "TEST: passed." in done["result"] and "- Fixed in 1 round." in done["result"]
    steps = {s["id"]: s for s in done["steps"]}
    assert list(steps) == [
        "lead",
        "build1",
        "save1",
        "build2",
        "save2",
        "test1",
        "review1a",
        "fix1",
        "resave1",
        "test2",
        "review2a",
    ]
    assert steps["lead"]["title"] == "Lead picked the team"
    assert [t["label"] for t in steps["lead"]["thinking"]] == ["Goal", "Approach"]
    assert "<thinking>" not in steps["lead"]["output"]
    assert all("<steps>" in s for s, _ in seen)  # every member was asked for its steps first
    assert steps["lead"]["team"] == [
        "Developer",
        "UI Designer",
        "Security Reviewer",
        "Debugger",
        "Tester",
    ]
    assert steps["build2"]["title"] == "UI Designer works on it"
    assert "Script error: undefinedFunction" in steps["test1"]["output"]
    assert "TEST: passed." in steps["test2"]["output"]
    # The second builder worked on the files the first one wrote; the reviewer saw the test.
    designer = next(p for s, p in seen if "Role: UI Designer" in s)
    assert "undefinedFunction()" in designer
    reviewer = next(p for s, p in seen if "Role: Security Reviewer" in s)
    assert "Script error: undefinedFunction" in reviewer
    assert done["used_calls"] == 6  # lead, 2 builders, review, fix, review again
    assert alerts(client, auth)[0]["message"].startswith("Built in")


def test_out_of_rounds_the_person_is_asked_once_and_decides(client, auth, tmp_path):
    browser_or_skip()
    runtime = setup(client, auth)
    allow_projects(client, auth, runtime, tmp_path)
    lead = (
        "GOAL: a todo list.\nTEAM:\n- builder | Developer | all of it\n- fixer | Debugger | fixes"
    )
    # "quick": one round. Still broken after it, the card asks with two buttons.
    seen = team_chat(
        runtime,
        lead,
        builders=[file_block(BROKEN)],
        fixes=[file_block(BROKEN + "<!-- try 2 -->"), file_block(GOOD)],
    )
    run_id = start(client, auth, team_plan("a quick todo list", "Quick Todo"))
    step = asked(client, auth, run_id)
    assert step and step["choices"] == ["Keep fixing", "Stop here"], step
    assert step["ask"] == (
        "Still 2 problems: The page shows a script error; adding an item does not work. "
        "Keep fixing (about 2 more model calls) or stop here?"
    ), step["ask"]
    # On the desktop too, linked to this step, so it is answered there (not "Crew not found").
    shown = [a for a in alerts(client, auth) if a.get("task_step_id") == step["id"]]
    assert shown and shown[0]["task_run_id"] == run_id and "Keep fixing" in shown[0]["message"]
    # In their own words, from the box under the buttons: it goes on, and the fixer is told.
    client.post(
        f"/api/v1/tasks/{run_id}/answer",
        headers=auth,
        json={"answer": "Yes continue, only fix the crash"},
    ).raise_for_status()
    done = wait(client, auth, run_id, timeout=240)
    last_fix = [p for s, p in seen if "Fix every problem" in s][-1]
    assert "[the_person_says]\nYes continue, only fix the crash" in last_fix
    assert done["status"] == "done", done
    assert "TEST: passed." in done["result"] and "- Fixed in 2 rounds." in done["result"]
    assert not any(s.get("choices") for s in done["steps"])
    # Answered, the question leaves the desktop.
    assert not [a for a in alerts(client, auth) if a.get("task_step_id") == step["id"]]

    # This time the person stops it: what was built is kept, its problems listed honestly.
    team_chat(
        runtime, lead, builders=[file_block(BROKEN)], fixes=[file_block(BROKEN + "<!-- 2 -->")]
    )
    run_id = start(client, auth, team_plan("a quick todo list", "Quick Two"))
    assert asked(client, auth, run_id)
    client.post(
        f"/api/v1/tasks/{run_id}/answer", headers=auth, json={"answer": "Stop here"}
    ).raise_for_status()
    done = wait(client, auth, run_id, timeout=120)
    assert done["status"] == "done" and "TEST: problems found." in done["result"]
    assert done["used_calls"] == 3  # lead, builder, one fix


def test_the_persons_call_is_read_from_a_button_or_their_own_words():
    from mavo.team_build import wants_more

    for go in ("Keep fixing", "Yes continue", "yes", "fix only the timer bug", "ok go on"):
        assert wants_more(go), go
    for stop in (
        "Stop here",
        "stop",
        "no",
        "No thanks",
        "enough",
        "that's fine",
        "good enough",
        "",
    ):
        assert not wants_more(stop), stop


def test_a_build_that_works_asks_nothing_and_a_stuck_team_stops(client, auth, tmp_path):
    browser_or_skip()
    runtime = setup(client, auth)
    allow_projects(client, auth, runtime, tmp_path)
    team_chat(runtime, "GOAL: a todo list.", builders=[file_block(GOOD)])
    done = wait(client, auth, start(client, auth, team_plan("a todo list", "Todo")), timeout=120)
    assert done["status"] == "done" and "TEST: passed." in done["result"]
    assert "Fixed in" not in done["result"] and not any(s.get("ask") for s in done["steps"])
    assert done["used_calls"] == 3  # lead, developer, reviewer: nothing to fix

    # The fixer changes nothing: the team stops instead of spending round after round.
    team_chat(
        runtime,
        "GOAL: a todo list.",
        builders=[file_block(BROKEN)],
        reviews=["1. It crashes. VERDICT: fix"],
        fixes=["NO CHANGES"],
    )
    done = wait(client, auth, start(client, auth, team_plan("a todo list", "Stuck")), timeout=120)
    assert done["status"] == "done", done
    assert "- Still open: the last fix changed nothing, so the team stopped." in done["result"]
    assert "- Still open: It crashes" in done["result"]
    # Lead, developer, reviewer, fixer, then one whole rewrite from the design before it stops.
    assert done["used_calls"] == 5
    assert any("writes it again, from the start" in s["title"] for s in done["steps"])


def test_a_cut_off_fix_is_tried_again_with_edits_and_nothing_built_is_lost(client, auth, tmp_path):
    browser_or_skip()
    runtime = setup(client, auth)
    allow_projects(client, auth, runtime, tmp_path)
    seen = team_chat(
        runtime,
        "GOAL: a todo list.\nTEAM:\n- builder | Developer | all\n- fixer | Debugger | fixes",
        builders=[file_block(BROKEN)],
        fixes=[
            "```file:index.html\n<html><body>the whole page again, until the reply ran out",
            "EDIT index.html\n<<<<<<< FIND\n<script>undefinedFunction()</script>\n=======\n"
            "<script>/* fixed */</script>\n>>>>>>> REPLACE\nCHANGES: removed the call.",
        ],
    )
    done = wait(client, auth, start(client, auth, team_plan("a todo list", "Cut")), timeout=180)
    assert done["status"] == "done", done
    steps = {s["id"]: s for s in done["steps"]}
    assert steps["resave1"]["status"] == "failed" and "cut off" in steps["resave1"]["error"]
    assert (
        steps["resave1b"]["status"] == "done"
        and "1 change to index.html" in steps["resave1b"]["output"]
    )
    again = [p for s, p in seen if "Fix every problem" in s][1]
    assert "Your last reply could not be used" in again and "EDIT blocks" in again
    assert "Script error" not in steps["test2"]["output"]

    # The budget runs out in the middle: the person still gets the app, with that said plainly.
    team_chat(
        runtime,
        "GOAL: a todo list.",
        builders=[file_block(BROKEN)],
        reviews=["1. Broken. VERDICT: fix"] * 9,
        fixes=[file_block(BROKEN + "<!--1-->")] * 9,
    )
    plan = team_plan("a todo list", "Budget")
    plan["model_calls"] = 2
    import mavo.team_build as tb

    real, tb.BUILD_CEILING = tb.BUILD_CEILING, 4
    try:
        done = wait(client, auth, start(client, auth, plan), timeout=180)
    finally:
        tb.BUILD_CEILING = real
    assert done["status"] == "done", done
    assert done["result"].startswith("Built in") and "TEST: problems found." in done["result"]
    assert (
        "- Still open: the team stopped early, the task's model-call budget ran out."
        in done["result"]
    )


def test_the_lead_asks_when_something_essential_is_unclear_and_the_build_goes_on(
    client, auth, tmp_path
):
    runtime = setup(client, auth)
    allow_projects(client, auth, runtime, tmp_path)
    designs = []

    def lead(prompt):
        designs.append(prompt)
        if "The person answered" not in prompt:
            return "QUESTION: Is this a web app or a command-line tool?"
        return "GOAL: a tracker. KIND: web."

    team_chat(runtime, lead, builders=[file_block(GOOD)])
    run_id = start(client, auth, team_plan("Build me a habit tracker", "Habits"))
    step = asked(client, auth, run_id)
    assert step and step["ask"] == "Is this a web app or a command-line tool?"
    assert (
        client.post(
            f"/api/v1/tasks/{run_id}/answer", headers=auth, json={"answer": "A web app"}
        ).status_code
        == 202
    )
    done = wait(client, auth, run_id, timeout=120)
    assert done["status"] == "done", done
    assert "The person answered: A web app" in designs[-1]
    assert done["used_calls"] == 4  # the question, the design, the developer, the reviewer


def test_the_build_team_gets_room_for_whole_files_and_nothing_else_does(client, auth):
    """A Pomodoro timer's index.html was cut off mid-line by a 4096-token reply limit."""
    from mavo.model_router import LONG_REPLY, reply_tokens

    runtime = setup(client, auth)
    room = []

    async def chat(provider, model, messages, system, credential_id=None):
        room.append((system.split("Role: ")[-1][:12], reply_tokens(), LONG_REPLY.get()))
        return "```file:index.html\n" + GOOD + "\n```" if "developer" in system else "ok"

    runtime.router.chat = chat
    plan = {
        "title": "t",
        "deliver": "d",
        "steps": [
            {"id": "c", "kind": "helper", "role": "coder", "title": "Code", "instructions": "x"},
            {
                "id": "w",
                "kind": "helper",
                "role": "writer",
                "title": "Write",
                "instructions": "x",
                "uses": ["c"],
            },
        ],
    }
    wait(client, auth, start(client, auth, plan))
    assert [(r[1], r[2]) for r in room] == [(16000, True), (4096, False)]
    assert reply_tokens() == 4096  # and it is back to normal afterwards


def test_asked_to_be_told_a_crew_shows_its_reports_on_the_desktop(planning):
    """The model drafted "remind me of the news every 30 minutes" with desktop alerts off."""
    from tests.test_scenarios import decide, route

    client, auth, runtime, _ = planning
    r = route(client, auth)
    proposal = {
        "template_id": "research",
        "name": "News Watch",
        "responsibilities": "Every review, the newest headlines.",
        "route_id": r["id"],
        "trigger_type": "interval",
        "review_interval_minutes": 30,
        "requested_notifications": False,
    }
    decide(planning, crew_proposal=proposal)
    told = turn(planning, "Remind me of the latest news every 30 minutes")["draft"]
    assert told["configuration_plan"]["draft"]["requested_notifications"] is True
    decide(planning, crew_proposal={**proposal, "name": "Quiet Watch"})
    quiet = turn(planning, "Keep an eye on the news every 30 minutes")["draft"]
    assert quiet["configuration_plan"]["draft"]["requested_notifications"] is False


def test_the_planner_is_told_to_build_with_the_team():
    from mavo.coordinator_contract import PlannerContext, coordinator_prompt

    text = coordinator_prompt(PlannerContext(local_time="2026-11-05T10:00:00+05:30"))
    assert "team.build" in text and "picks the team" in text and "BUILDING SOFTWARE" in text


# ---- scheduled Crews are given only what is new -------------------------------------------------


class Feeds:
    def __init__(self):
        self.items = []

    async def read(self, provider, resource):
        return {"provider": provider, "resource": resource, "items": [dict(i) for i in self.items]}


def test_a_scheduled_crew_is_given_only_items_it_has_not_reported(planning):
    """An inbox summary repeated the same mails every half hour: "only new since the last review"
    was left to the model's memory."""
    from datetime import timedelta

    from mavo.database import CrewInstance, utcnow
    from tests.test_crew_ask import news_crew

    client, auth, runtime, _ = planning
    crew = news_crew(client, auth, runtime, name="News Watch")
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew)
        stored.permissions = [*stored.permissions, "feeds:bbc-world:read"]
        session.commit()
    runtime.connectors.feeds.configure(["bbc-world"])
    feeds = Feeds()
    runtime.connectors.read = feeds.read

    def review(items):
        feeds.items = items
        with runtime.factory() as session:
            stored = session.get(CrewInstance, crew)
            stored.runtime_state, stored.next_run_at = "waiting", utcnow() - timedelta(seconds=5)
            session.commit()
        chat = run_work(runtime, crew, checkpoint("Reported."))
        with runtime.factory() as session:
            after = session.get(CrewInstance, crew)
            why = (
                after.runtime_state,
                after.last_runtime_error,
                after.is_running,
                after.permissions,
            )
        assert chat.await_args_list, why
        return chat.await_args_list[0].args[2][0]["content"]

    first = review([{"id": "a", "title": "Story A"}, {"id": "b", "title": "Story B"}])
    assert "Story A" in first and "Story B" in first and "ONLY the new ones" in first
    second = review(
        [
            {"id": "c", "title": "Story C"},
            {"id": "a", "title": "Story A"},
            {"id": "b", "title": "Story B"},
        ]
    )
    assert "Story C" in second and "Story A" not in second and "Story B" not in second
    third = review([{"id": "c", "title": "Story C"}, {"id": "a", "title": "Story A"}])
    assert "Story C" not in third and "NO new emails or items" in third
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew).observation["seen_items"] == ["a", "b", "c"]


READER = """<!doctype html><html><body><input type=search id=q placeholder="Search all feeds"><ul id=l></ul>
<script>
const items = ['Markets rally on rate hopes', 'Monsoon withdraws early', 'Elections results tonight', 'Markets close higher again'];
const draw = () => { l.innerHTML = ''; items.filter(x => x.toLowerCase().includes(q.value.toLowerCase())).forEach(x => { const li = document.createElement('li'); li.textContent = x; l.appendChild(li); }); };
q.oninput = draw; draw();
</script></body></html>"""


def test_a_reader_is_searched_not_added_to(tmp_path):
    """An RSS reader was failed: its search box was typed into as if it added items ("Typing an
    item ... did NOT put it on the page"), and the team spent rounds "fixing" it."""
    browser_or_skip()
    page = tmp_path / "index.html"
    page.write_text(READER, encoding="utf-8")
    report = asyncio.run(check_web(page))
    assert not any("Typing an item" in line for line in report), report
    assert 'Searching for "markets" narrowed the list from 4 to 2.' in report, report
    # A search box that does nothing is a real problem, said as one.
    page.write_text(READER.replace("q.oninput = draw;", ""), encoding="utf-8")
    broken = asyncio.run(check_web(page))
    assert any("did NOT change anything on the page" in line for line in broken), broken
    from mavo.team_build import said_plainly

    assert said_plainly([line for line in broken if "NOT" in line]) == [
        "searching does not filter anything"
    ]


# ── Elided code is never shipped; a reply with no change is retried; a stuck app is rewritten ─
SKELETON = (
    "<!doctype html><html><head><style>...</style></head><body><div id=root></div>"
    "\n<script>\nconst x = 1;\n...\n</script></body></html>"
)


def project_html(tmp_path, name):
    found = list(tmp_path.rglob("index.html"))
    return next(
        (p.read_text(encoding="utf-8") for p in found if name.lower() in str(p).lower()), ""
    )


def test_code_left_out_is_never_saved_and_the_builder_writes_it_in_full(client, auth, tmp_path):
    browser_or_skip()
    runtime = setup(client, auth)
    allow_projects(client, auth, runtime, tmp_path)
    seen = team_chat(
        runtime, "GOAL: a todo list.", builders=[file_block(SKELETON), file_block(GOOD)]
    )
    done = wait(client, auth, start(client, auth, team_plan("a todo list", "Elided")), timeout=120)
    assert done["status"] == "done" and "TEST: passed." in done["result"], done
    assert "...</style>" not in project_html(tmp_path, "Elided")  # the skeleton was never written
    assert (
        "left code out (index.html line 1" in seen[2][1] or "left code out" in seen[2][1]
    )  # told exactly why


def test_a_model_that_keeps_leaving_code_out_ends_plainly_not_with_a_blank_page(
    client, auth, tmp_path
):
    browser_or_skip()
    runtime = setup(client, auth)
    allow_projects(client, auth, runtime, tmp_path)
    team_chat(runtime, "GOAL: a todo list.", builders=[file_block(SKELETON), file_block(SKELETON)])
    done = wait(client, auth, start(client, auth, team_plan("a todo list", "Lazy")), timeout=120)
    assert done["status"] == "failed", done
    assert "kept leaving code out" in (done["error"] or "")
    assert project_html(tmp_path, "Lazy") == ""


def test_a_fix_that_only_describes_the_change_is_asked_for_the_change_itself(
    client, auth, tmp_path
):
    browser_or_skip()
    runtime = setup(client, auth)
    allow_projects(client, auth, runtime, tmp_path)
    seen = team_chat(
        runtime,
        "GOAL: a todo list.\nTEAM:\n- builder | Developer | all\n- fixer | Debugger | fixes",
        builders=[file_block(BROKEN)],
        fixes=[
            "1. arXiv AI http://export.arxiv.org/rss/cs.AI\n2. BBC Tech https://feeds.bbci.co.uk/news/technology/rss.xml",
            "EDIT index.html\n<<<<<<< FIND\n<script>undefinedFunction()</script>\n=======\n"
            "<script>/* fixed */</script>\n>>>>>>> REPLACE\nCHANGES: removed the call.",
        ],
    )
    done = wait(client, auth, start(client, auth, team_plan("a todo list", "Plan")), timeout=180)
    assert done["status"] == "done", done
    retry = [prompt for system, prompt in seen if "changed no file" in prompt]
    assert retry, "the fixer was told its reply changed nothing"
    assert "undefinedFunction" not in project_html(tmp_path, "Plan")
