"""What came back cut or wrong from "improve my career prospects": a report ending "(accurac", a
chat reply ending "publicly visible proj", and an agent asking for a Google sign-in to search."""

from types import SimpleNamespace

from mavo.crew_planner import tidy_reply
from mavo.schemas import CrewDraft, SuggestedAction, searched_not_opened
from mavo.site_skills import ANSWERING
from mavo.tasks import ANSWER_CHARS, clip


def test_a_long_report_is_kept_and_never_cut_mid_word():
    report = ("A full sentence about the shortlist. " * 300).strip()  # about 11,000 characters
    assert clip(report, ANSWER_CHARS) == report  # 6,000 used to cut it
    huge = "Paragraph one ends here.\n\n" + ("word " * 6000)
    kept = clip(huge, 5000)
    assert kept.endswith("(Cut short here: the rest was longer than Mavo keeps.)")
    assert "accurac" not in kept and len(kept) < 5100


def test_the_chat_reply_beside_a_draft_ends_on_a_whole_word():
    reasoning = (
        "Here's my reasoning: with a 2027 CSE graduation, React and Python, and a strong CGPA, "
        "your scarce resources are time and internship traction, not more breadth. So the "
        "highest-impact levers are (1) one real, shippable, publicly visible project with users, "
        "(2) a steady application pipeline, and (3) interview practice."
    )
    decision = SimpleNamespace(
        reply=reasoning, questions=[], crew_proposal=object(), crew_proposals=[], task=None
    )
    short = tidy_reply(decision)
    assert short.endswith("…") or short.endswith(".")
    assert not short.rstrip("…").endswith("proj")
    assert short.rstrip("…") in reasoning


def test_a_search_engine_is_searched_never_a_site_to_sign_in_to():
    actions = [
        SuggestedAction(operation="browser.open", domains=["www.google.com", "www.naukri.com"]),
        SuggestedAction(operation="browser.read", domains=["google.co.in"]),
    ]
    kept = searched_not_opened(actions)
    assert [(a.operation, a.domains) for a in kept] == [
        ("browser.open", ["www.naukri.com"]),
        ("browser.search", []),
    ]
    draft = CrewDraft.model_validate(
        {
            "template_id": "research",
            "name": "Signal Scout",
            "responsibilities": "Watch hiring signals for 2027 graduates each week.",
            "trigger_type": "interval",
            "review_interval_minutes": 10080,
            "requested_actions": [
                {"operation": "browser.open", "domains": ["www.google.com"]},
                {"operation": "browser.read", "domains": ["www.google.com"]},
            ],
        }
    )
    assert all("google" not in " ".join(a.domains) for a in draft.requested_actions)
    assert any(a.operation == "browser.search" for a in draft.requested_actions)


def test_research_lists_real_entries_and_marks_what_no_page_says():
    assert "aggregator" in ANSWERING and "unconfirmed" in ANSWERING
