"""What a live run of Mavo core's crew design and a real browser crew turned up (2026-11-11):
designs that named a capability slightly wrong failed with no hint which; a mail reader marked
for web-source checks would fail every run; a code-fixing crew had no reviewer; a site limiting
requests was retried until the agent ran out of steps; and asking to wait for a page was refused."""

import json

import pytest
from pydantic import ValidationError

from mavo.coordinator_contract import PlannerContext, coordinator_prompt
from mavo.crew_composition import AgentSpec
from mavo.crew_workers import parse_checkpoint
from mavo.desktop_actions import RATE_LIMITED


def agent(**over):
    base = dict(key="a", name="A", role="Reads", instructions="Do it", completion_criteria="Done")
    return AgentSpec(**{**base, **over})


def test_a_capability_written_without_google_is_put_right():
    made = agent(capabilities=["gmail:read", "calendar:write", "notifications:show"])
    assert made.capabilities == ["google:calendar:write", "google:gmail:read", "notifications:show"]


def test_an_unsupported_capability_or_action_is_named_so_it_can_be_corrected():
    with pytest.raises(ValidationError) as bad:
        agent(capabilities=["google:gmail:read", "inbox:magic"])
    assert "inbox:magic" in str(bad.value) and "google:gmail:read" in str(bad.value)  # the hint
    with pytest.raises(ValidationError) as bad:
        agent(actions=["gmail.teleport"])
    assert "gmail.teleport" in str(bad.value)


def test_a_mail_reader_is_never_held_to_web_source_checks():
    reader = agent(capabilities=["google:gmail:read"], verification="source_evidence")
    assert reader.verification == "deliverable"  # it has no feed item or page to cite
    news = agent(capabilities=["feeds:bbc-tech:read"], verification="source_evidence")
    assert news.verification == "source_evidence"
    browser = agent(actions=["browser.open", "browser.read"], verification="source_evidence")
    assert browser.verification == "source_evidence"
    inherits = agent(verification="source_evidence")  # its crew's feed access, not its own
    assert inherits.verification == "source_evidence"


def test_core_splits_readers_from_writers_and_reviews_code_changes():
    prompt = coordinator_prompt(PlannerContext(local_time="2026-11-11T09:00:00+05:30"))
    assert "plan my day from mail = mail reader (gmail read) then scheduler" in prompt
    assert "never reads the raw content itself" in prompt
    assert "ANY change to the person's code or files" in prompt
    assert "a fixer never reviews its own change" in prompt


@pytest.mark.parametrize(
    "text,limited",
    [
        ("Sorry, we're not able to serve your requests this quickly.", True),
        ("429 Too Many Requests", True),
        ("You are being rate limited. Try again later.", True),
        ("Lobsters | The hardest tarball | .name grandfathered", False),
    ],
)
def test_a_site_limiting_requests_is_recognised(text, limited):
    assert bool(RATE_LIMITED.search(text)) is limited


def test_asking_to_wait_for_a_page_is_never_a_forbidden_action():
    reply = json.dumps(
        {
            "state": "continue",
            "summary": "Loading",
            "next_step": "read",
            "actions": [
                {"operation": "browser.open", "arguments": {"url": "https://example.com"}},
                {"operation": "browser.wait", "arguments": {}},
                {"operation": "browser.read", "arguments": {}},
            ],
        }
    )
    allowed = {"browser.open": {}, "browser.read": {}}
    checkpoint = parse_checkpoint(reply, allowed)
    assert [a.operation for a in checkpoint.actions] == ["browser.open", "browser.read"]
    granted = parse_checkpoint(reply, {**allowed, "browser.wait": {}})
    assert [a.operation for a in granted.actions] == [
        "browser.open",
        "browser.wait",
        "browser.read",
    ]
