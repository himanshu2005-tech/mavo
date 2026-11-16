# ruff: noqa: E501 - fixture page text and links are long by nature
"""Listings the agent really saw are accepted, however the model writes them; anything it did not
see is still rejected. An internship crew failed setup with "No source evidence matched" for
listings that were right there: a space or dash differed, the page showed the link in short form,
or the model forgot the evidence list and only wrote links in its report."""

from datetime import timedelta

from mavo.database import ActionRequest, utcnow
from mavo.outcomes import derive_evidence, plain_url, quoted_in, verify_sources
from tests.test_crew_v2 import seed
from tests.test_multi_agent import make_session

PAGE = {
    "url": "https://unstop.com/internship?oppstatus=open",
    "view": "PAGE https://unstop.com/internship?oppstatus=open\n"
    '[e12] link "AI & React Developer Internship" → /internships/ai-react-developer-internship-smaranaiin-1805059\n'
    "AI & React Developer Internship\nSmaranAI.in\nWork from Home – ₹2.25K - 3.5K/Month\n12 Days Left",
}


def saw(factory, crew_id, result, operation="browser.read"):
    with factory() as session:
        session.add(
            ActionRequest(
                crew_id=crew_id,
                account_subject="local",
                operation=operation,
                source="crew",
                arguments={},
                fingerprint="f" + operation + str(len(str(result))),
                status="completed",
                result=result,
            )
        )
        session.commit()


def setup(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        crew.permissions = [*crew.permissions, "action:browser.read"]
        session.commit()
        crew_id = crew.id
    began = utcnow() - timedelta(seconds=5)
    saw(factory, crew_id, PAGE)
    return factory, crew_id, began


def test_spacing_dashes_and_link_forms_do_not_matter():
    assert quoted_in("work from home - Rs 2.25k", "Work from Home – ₹2.25K")
    assert quoted_in("AI & React Developer Internship ... 12 days left", PAGE["view"])
    assert not quoted_in("Paid Python Internship", PAGE["view"])
    assert plain_url("http://www.unstop.com/internships/x/?utm_source=a") == plain_url(
        "https://unstop.com/internships/x"
    )


def test_a_listing_seen_with_a_short_link_and_reworded_spacing_is_accepted(tmp_path):
    factory, crew_id, began = setup(tmp_path)
    claim = {
        "kind": "listing",
        "url": "https://unstop.com/internships/ai-react-developer-internship-smaranaiin-1805059",
        "title": "AI & React Developer Internship",
        "quote": "AI & React Developer Internship SmaranAI.in Work from Home - ₹2.25K - 3.5K/Month",
    }
    with factory() as session:
        result = verify_sources(session, crew_id, began, [claim], require_listings=True)
    assert result["evidence"] and result["step"] == "evidence_checked"


def test_an_invented_listing_is_still_rejected(tmp_path):
    factory, crew_id, began = setup(tmp_path)
    fake = {
        "kind": "listing",
        "url": "https://unstop.com/internships/made-up-999",
        "title": "Senior Python Intern",
        "quote": "Senior Python Intern at Google, Rs 90K/month, apply now",
    }
    with factory() as session:
        result = verify_sources(session, crew_id, began, [fake], require_listings=True)
    assert not result["evidence"] and result["rejected_evidence_count"] == 1


def test_mavo_builds_the_evidence_from_a_report_that_only_gives_links(tmp_path):
    factory, crew_id, began = setup(tmp_path)
    report = (
        "Found on Unstop:\n"
        "1) AI & React Developer Internship — SmaranAI.in — "
        "https://unstop.com/internships/ai-react-developer-internship-smaranaiin-1805059\n"
        "2) Quantum Intern — Nowhere — https://unstop.com/internships/quantum-intern-1\n"
    )
    with factory() as session:
        derived = derive_evidence(session, crew_id, began, report)
        assert [d["url"].rsplit("/", 1)[-1] for d in derived] == [
            "ai-react-developer-internship-smaranaiin-1805059"
        ]
        result = verify_sources(session, crew_id, began, derived, require_listings=True)
    assert len(result["evidence"]) == 1  # the seen one; the one not on any page read is left out


def test_web_search_results_count_as_seen(tmp_path):
    factory, crew_id, began = setup(tmp_path)
    saw(
        factory,
        crew_id,
        {
            "results": [
                {
                    "title": "React Intern at Acme - Unstop",
                    "url": "https://unstop.com/internships/react-intern-acme-77",
                    "snippet": "React Intern at Acme. Remote. Stipend 10K per month. Apply by 30 Nov.",
                }
            ]
        },
        operation="browser.search",
    )
    claim = {
        "kind": "listing",
        "url": "https://unstop.com/internships/react-intern-acme-77",
        "title": "React Intern at Acme",
        "quote": "React Intern at Acme. Remote. Stipend 10K per month.",
    }
    with factory() as session:
        assert verify_sources(session, crew_id, began, [claim], require_listings=True)["evidence"]


def test_evidence_is_found_however_the_report_is_laid_out(tmp_path):
    """**Role:** on one line and **Apply:** <link> on another: the page's own link names it."""
    factory, crew_id, began = setup(tmp_path)
    report = (
        "1. **Role:** AI & React Developer Internship\n"
        "**Company:** SmaranAI.in | **Mode:** Work from Home\n"
        "**Apply:** https://unstop.com/internships/ai-react-developer-internship-smaranaiin-1805059\n"
    )
    with factory() as session:
        derived = derive_evidence(session, crew_id, began, report)
        assert derived and derived[0]["title"] == "AI & React Developer Internship"
        assert verify_sources(session, crew_id, began, derived, require_listings=True)["evidence"]
