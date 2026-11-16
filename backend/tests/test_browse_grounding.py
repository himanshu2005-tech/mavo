# ruff: noqa: E501 - fixture page text is long by nature
"""A browser agent's short answer, proven by the page itself. "Credits: 1,240" failed a whole run
("No source evidence matched") because a quote under 24 characters was dropped; Mavo now finds
each value the report states on a page the agent read and quotes that page's own line. And each
browser step becomes a card the person can follow."""

from datetime import timedelta

from mavo.browse_cards import card_of, page_of
from mavo.database import utcnow
from mavo.outcomes import ground_evidence, values_in, verify_sources
from tests.test_crew_v2 import seed
from tests.test_listing_evidence import saw
from tests.test_multi_agent import make_session

BILLING = {
    "url": "https://airouter.in/dashboard/billing",
    "title": "Billing · AIRouter",
    "text": "AIRouter\nDashboard\nBilling\nCredits remaining\n1,240\nTop up\nUsage this month\n$12.50",
}


def setup(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        crew.permissions = [*crew.permissions, "action:browser.read"]
        session.commit()
        crew_id = crew.id
    began = utcnow() - timedelta(seconds=5)
    saw(factory, crew_id, BILLING)
    return factory, crew_id, began


def test_the_values_a_report_states():
    assert values_in(
        'You have 1,240 credits left; usage is $12.50 (85%). Plan: "Pro Monthly".'
    ) == [
        "1,240",
        "$12.50",
        "85%",
        "Pro Monthly",
    ]
    assert values_in("Checked at 9 on day 12.") == []  # too small to tie to a page


def test_a_short_answer_is_proven_by_the_pages_own_line(tmp_path):
    factory, crew_id, began = setup(tmp_path)
    with factory() as session:
        grounded = ground_evidence(session, crew_id, began, "Credits left: 1,240.")
        assert grounded == [
            {
                "kind": "source",
                "url": "https://airouter.in/dashboard/billing",
                "quote": "Billing Credits remaining 1,240",  # the label above it, from the page
            }
        ]
        result = verify_sources(session, crew_id, began, grounded)
    assert result["step"] == "evidence_checked" and result["evidence"]


def test_a_value_on_no_page_read_proves_nothing(tmp_path):
    factory, crew_id, began = setup(tmp_path)
    with factory() as session:
        assert ground_evidence(session, crew_id, began, "Credits left: 9,999.") == []


def test_pages_read_before_this_run_do_not_count(tmp_path):
    factory, crew_id, _ = setup(tmp_path)
    with factory() as session:
        assert ground_evidence(session, crew_id, utcnow() + timedelta(minutes=1), "1,240") == []


def test_each_browser_step_is_a_card():
    opened = card_of(
        "browser.open",
        {"url": "https://airouter.in"},
        {"status": "completed", "result": {"url": "https://www.airouter.in/", "title": "AIRouter"}},
    )
    assert opened["tool"] == "browser.open" and opened["outcome"] == "done"
    assert opened["about"] == "airouter.in" and opened["result"] == "AIRouter"
    lost = card_of(
        "browser.navigate",
        {"goal": "credits"},
        {"status": "failed", "error": "No link named credits.", "result": None},
    )
    assert lost["outcome"] == "failed" and lost["result"] == "No link named credits."
    wall = card_of(
        "browser.read",
        {},
        {
            "status": "completed",
            "result": {
                "url": "https://airouter.in/login",
                "title": "Sign in",
                "sign_in_required": {"host": "airouter.in"},
            },
        },
    )
    assert wall["outcome"] == "asked" and "sign in" in wall["result"]
    assert card_of("calendar.create", {}, {"status": "completed"}) is None
    assert page_of("https://www.airouter.in/dashboard/billing/") == "airouter.in/dashboard/billing"
