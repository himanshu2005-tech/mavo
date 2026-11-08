# ruff: noqa: E501, F811
"""Built-in skills: a site's well-known job done by Mavo's own code, with no model."""

import asyncio

import pytest

from mavo.site_skills import (
    SKILLS,
    FormParams,
    SkillNeedsYou,
    SkillStop,
    build_form,
    form_title,
    question_titles,
    report,
)
from mavo.task_plan import TaskPlan
from tests.test_multi_agent import with_page
from tests.test_tasks import alerts, allow_browser, plan, scripted, setup, start, wait

SKILL = "microsoft_forms.create"
PARAMS = {"title": "Student form", "questions": ["Name", "Email"]}


def skill_step(**extra):
    base = {"id": "make", "kind": "skill", "title": "Make the form", "instructions": "Make it"}
    return base | {"skill": SKILL, "params": PARAMS} | extra


# ---- the plan ------------------------------------------------------------------------------


def test_a_skill_step_takes_what_it_needs_from_the_skill_not_from_the_plan():
    made = TaskPlan.model_validate(
        plan(skill_step(actions=["terminal.run"], sites=["evil.example"]))
    )
    one = made.steps[0]
    assert one.kind == "skill" and one.skill == SKILL
    assert one.actions == list(SKILLS[SKILL].actions) and one.sites == ["forms.office.com"]
    assert one.params == PARAMS


def test_a_skill_step_that_does_not_fit_still_leaves_a_plan_an_agent_can_do():
    # An unknown skill, or parameters that do not fit: the instructions still say what to do.
    for bad in (
        skill_step(skill="nonsense.run", sites=["forms.office.com"]),
        skill_step(params={"title": "", "questions": []}, sites=["forms.office.com"]),
        skill_step(params="nonsense", sites=["forms.office.com"]),
    ):
        one = TaskPlan.model_validate(plan(bad)).steps[0]
        assert one.kind == "agent" and one.skill is None and one.params == {}
        assert one.actions == ["browser.open", "browser.read"]
    # With nothing at all to go on it only thinks.
    assert (
        TaskPlan.model_validate(plan(skill_step(skill="nope", params={}))).steps[0].kind == "helper"
    )


def test_the_planner_is_told_which_skills_exist_and_to_prefer_them():
    from mavo.coordinator_contract import PlannerContext, coordinator_prompt

    text = coordinator_prompt(PlannerContext(local_time="2026-11-04T10:00:00+05:30"))
    assert "kind skill" in text and SKILL in text and "ALWAYS prefer a skill" in text


def test_form_parameters_are_tidied_not_trusted():
    tidy = FormParams.model_validate(
        {"title": "  T  ", "questions": [" Name ", "", 5, None, "Email"]}
    )
    assert tidy.title == "T" and tidy.questions == ["Name", "5", "Email"]
    for bad in (
        {"title": "T", "questions": []},
        {"title": "", "questions": ["a"]},
        {"questions": ["a"]},
    ):
        with pytest.raises(ValueError):
            FormParams.model_validate(bad)


# ---- running -------------------------------------------------------------------------------


def fake_skill(monkeypatch, outcome):
    """A skill whose browser work is replaced, to see how a task treats each way it can end."""
    calls = []

    async def run(desktop, rule, owner, params):
        calls.append((rule["mode"], owner, params.title))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    real = SKILLS[SKILL]
    monkeypatch.setitem(SKILLS, SKILL, type(real)(**{**real.__dict__, "run": run}))
    return calls


def enable(client, auth, *operations):
    for operation in operations:
        client.put(
            "/api/v1/action-policies",
            headers=auth,
            json={
                "operation": operation,
                "rule": {"enabled": True, "mode": "unrestricted", "domains": []},
            },
        ).raise_for_status()


def test_a_skill_step_runs_with_no_model_and_its_answer_is_the_result(client, auth, monkeypatch):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    enable(client, auth, "browser.click", "browser.fill")
    seen = scripted(runtime)
    calls = fake_skill(monkeypatch, "https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=abc")
    done = wait(client, auth, start(client, auth, plan(skill_step(), model_calls=6)))
    assert done["status"] == "done" and done["result"].endswith("?id=abc"), done
    assert done["used_calls"] == 0 and not seen["crew_prompts"] and not seen["helper_prompts"]
    assert calls and calls[0][2] == "Student form" and calls[0][1].startswith("task:")
    assert done["steps"][0]["kind"] == "skill" and alerts(client, auth)[0]["message"].endswith(
        "?id=abc"
    )


def test_a_skill_that_needs_the_person_asks_them_with_the_address_and_one_that_fails_says_why(
    client, auth, monkeypatch
):
    setup(client, auth)
    allow_browser(client, auth)
    enable(client, auth, "browser.click", "browser.fill")
    fake_skill(monkeypatch, SkillNeedsYou("Microsoft needs you to sign in to Forms."))
    needs = wait(client, auth, start(client, auth, plan(skill_step())))
    assert needs["status"] == "needs_you" and needs["sign_in_url"] == "https://forms.office.com"
    assert (
        "Sign in at https://forms.office.com" in needs["error"]
        and "press Try again" in needs["error"]
    )
    fake_skill(monkeypatch, SkillStop("Microsoft Forms did not show the form designer."))
    failed = wait(client, auth, start(client, auth, plan(skill_step())))
    assert failed["status"] == "failed" and "did not show the form designer" in failed["error"]


def test_a_skill_only_runs_with_the_actions_the_person_turned_on(client, auth, monkeypatch):
    setup(client, auth)
    calls = fake_skill(monkeypatch, "x")
    done = wait(client, auth, start(client, auth, plan(skill_step())))  # nothing is on yet
    assert done["status"] == "needs_you" and not calls
    assert (
        "Turn on in Actions (Defaults)" in done["error"] and "Press Try again" not in done["error"]
    )
    assert "Click element" in done["error"] and "Fill field" in done["error"]


# ---- the page logic, against a stand-in designer ---------------------------------------------

# It behaves as the real one does where it matters: the title and each question are edited in boxes
# that only count what is TYPED (key by key), what was typed is kept in the browser's storage
# ("saved"), and a reload shows what was saved.
DESIGNER = """<html><body>
<div id="head"></div>
<div id="quick" role="button" tabindex="0" aria-label="Quick start with">Quick start with</div>
<div id="types" hidden>
  <div role="button" tabindex="0" aria-label="Choice">Choice</div>
  <div role="button" tabindex="0" aria-label="Text" id="text">Text</div></div>
<div id="questions"></div>
<div id="add" role="button" tabindex="0" aria-label="Add new question" hidden>Add</div>
<div id="collect" role="button" tabindex="0" aria-label="Collect responses">Collect responses</div>
<div id="dialog" role="dialog" hidden>Send and collect responses. Only people in Test Org can respond.
  <input aria-label="Link for Collecting Responses" readonly value="https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=abc"></div>
<script>
const $ = id => document.getElementById(id);
let S = JSON.parse(localStorage.getItem('saved') || '{"t":"Untitled form","q":[]}');
const save = () => localStorage.setItem('saved', JSON.stringify(S));
// Only typed keys count: a box whose text was set in one go (fill) is never heard by the editor.
const typed = (box, onText) => box.addEventListener('keydown', () => setTimeout(() => onText(box.innerText), 0));
function render(active) {
  $('head').innerHTML = '';
  const t = document.createElement('div');
  if (active === 'title') {
    t.setAttribute('role', 'textbox'); t.setAttribute('aria-label', 'Form title');
    t.contentEditable = 'true'; t.innerText = S.t;
    typed(t, text => { S.t = text; save(); });
  } else {
    t.setAttribute('role', 'button'); t.tabIndex = 0; t.innerText = S.t;
    t.setAttribute('aria-label', 'Form title ' + S.t);
    t.onclick = () => { render('title'); $('head').firstChild.focus(); $('types').hidden = true; };
  }
  $('head').appendChild(t);
  $('questions').innerHTML = '';
  S.q.forEach((q, i) => {
    const e = document.createElement('div');
    if (active === i) {
      e.setAttribute('role', 'textbox'); e.contentEditable = 'true'; e.innerText = q;
      e.setAttribute('aria-label', 'Question title ' + (i + 1) + ' Input your question title here');
      typed(e, text => { if (window.__scramble) { e.innerText = 'scrambled'; } else { S.q[i] = text; save(); } });
    } else {
      e.setAttribute('role', 'button'); e.tabIndex = 0; e.innerText = q;
      e.setAttribute('aria-label', (i + 1) + '. Question title ' + q + ' Single line text');
    }
    $('questions').appendChild(e);
  });
  $('quick').hidden = S.q.length > 0; $('add').hidden = S.q.length === 0;
}
render(null);
$('quick').onclick = () => { $('types').hidden = false; };
$('add').onclick = () => { $('types').hidden = false; };
$('text').onclick = () => {
  S.q.push(''); save(); render(S.q.length - 1);
  document.querySelector('#questions [role=textbox]:last-child').focus();
  $('types').hidden = true;
};
$('collect').onclick = () => { $('dialog').hidden = false; };
</script></body></html>"""


def test_the_form_is_built_typed_saved_read_back_and_its_link_returned():
    async def work(desktop, page):
        link, dialog = await build_form(desktop, page, FormParams(**PARAMS))
        return link, dialog, await question_titles(page), await form_title(page)

    link, dialog, questions, title = with_page(DESIGNER, work)
    assert link == "https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=abc"
    assert questions == ["Name", "Email"] and title == "Student form"
    assert "Only people in Test Org can respond" in dialog
    assert report(link, dialog, "Student A").splitlines() == [
        link,
        "Made in the account Student A. Only people in Test Org can respond (Microsoft's default); change it under Collect responses.",
    ]
    assert report(link, "Anyone can respond", None) == link  # nothing to add: just the link


def test_a_form_whose_edits_were_not_saved_is_not_reported_as_made():
    """The first version set the text in one go. The page SHOWED it, so reading the page back said
    all was well; Microsoft had kept an untitled form with two empty questions. Only what is there
    after a reload counts."""

    async def work(desktop, page):
        await page.evaluate("window.__scramble = true")  # the editor does not keep the questions
        with pytest.raises(SkillStop, match="did not keep what was typed"):
            await build_form(desktop, page, FormParams(**PARAMS))
        return True

    assert with_page(DESIGNER, work)


def test_a_designer_that_never_shows_stops_with_a_plain_reason():
    async def work(desktop, page):
        with pytest.raises(SkillStop, match="did not show the form designer"):
            await asyncio.wait_for(build_form(desktop, page, FormParams(**PARAMS)), 90)
        return True

    assert with_page("<html><body>Not a form designer</body></html>", work)
