"""Searching a site instead of reporting its front page: a job watcher opened LinkedIn,
Internshala and Naukri, saw no listings on the homepages and said "nothing found"; its sample run
looked for links named "internshala.com" on LinkedIn and "found" a university page."""

from types import SimpleNamespace

from mavo.site_search import guide, known, search_url
from mavo.trial import next_move, trail_of


def test_known_job_sites_get_their_search_results_page():
    assert (
        search_url("linkedin.com", "frontend intern", "Hyderabad")
        == "https://www.linkedin.com/jobs/search/?keywords=frontend+intern&location=Hyderabad"
    )
    assert (
        search_url("www.naukri.com", "React developer internship", "Hyderabad")
        == "https://www.naukri.com/react-developer-internship-jobs-in-hyderabad"
    )
    assert search_url("naukri.com", "react", "") == "https://www.naukri.com/react-jobs"
    assert search_url("https://internshala.com/", "full stack", "") == (
        "https://internshala.com/internships/keywords-full-stack/"
    )
    assert known("in.indeed.com") == "indeed.com" and known("example.com") is None
    assert search_url("example.com", "x") is None and search_url("linkedin.com", " ") is None


def test_agents_are_told_to_search_and_given_the_pages_of_their_sites():
    text = guide({"linkedin.com", "naukri.com", "example.org"})
    assert "Never report 'nothing found' from a front page" in text
    assert "linkedin.com (jobs)" in text and "naukri.com (jobs)" in text
    assert "example.org" not in text and "internshala" not in text  # only its own sites
    assert "KNOWN SEARCH PAGES" not in guide({"example.org"})  # any site: the search box rule


def act(operation, **arguments):
    return SimpleNamespace(operation=operation, arguments=arguments)


SITES = ["internshala.com", "linkedin.com", "naukri.com"]


def test_the_sample_run_opens_another_allowed_site_instead_of_clicking_a_lookalike_link():
    assert next_move([act("browser.navigate", goal="internshala.com")], SITES) == (
        "open",
        "https://internshala.com",
    )
    url = "https://www.naukri.com/react-jobs-in-hyderabad"
    assert next_move([act("browser.open", url=url)], SITES) == ("open", url)
    assert next_move([act("browser.open", url="https://evil.example/x")], SITES) is None
    assert next_move([act("browser.navigate", goal="jobs")], SITES) == ("goal", "jobs")
    assert next_move([], SITES) is None


def test_the_sample_runs_trail_reads_plainly():
    trail = trail_of(
        "linkedin.com",
        [("naukri.com", ["https://www.naukri.com/react-jobs-in-hyderabad"]), ("Jobs", ["Jobs"])],
        True,
    )
    assert trail == [
        "Opened linkedin.com",
        "Opened naukri.com search results",
        "Found Jobs (Jobs)",
        "Read it and wrote the result",
    ]


def test_a_site_without_a_search_address_gets_its_listings_page():
    """Unstop ignores ?searchTerm: its internships page lists them all, filtered on the page."""
    from mavo.site_search import listing_page

    assert listing_page("unstop.com") == "https://unstop.com/internships"
    assert search_url("unstop.com", "react intern", "Hyderabad") == "https://unstop.com/internships"
    assert listing_page("linkedin.com") is None  # it needs words: a search address, not a list
    assert (
        "unstop.com (internships, filter by category on the page): https://unstop.com/internships"
        in guide({"unstop.com"})
    )
