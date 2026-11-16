"""Searching a site instead of staring at its front page.

A job watcher opened linkedin.com, internshala.com and naukri.com, saw no listings on the
homepages and reported "nothing found". Listings live on search-result pages. For the sites people
ask about most, Mavo knows the address of those pages and builds it from the words and the place
(no model, no clicking); for any other site the agent is told to use the site's own search box.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urlsplit

# host: (what it searches, address with {q} (spaces as +), {q_dash}, {place}, {place_dash}).
SEARCH_PAGES: dict[str, tuple[str, str]] = {
    "linkedin.com": ("jobs", "https://www.linkedin.com/jobs/search/?keywords={q}&location={place}"),
    "naukri.com": ("jobs", "https://www.naukri.com/{q_dash}-jobs-in-{place_dash}"),
    "internshala.com": ("internships", "https://internshala.com/internships/keywords-{q_dash}/"),
    "indeed.com": ("jobs", "https://in.indeed.com/jobs?q={q}&l={place}"),
    # No search address (a ?searchTerm is ignored): the full listings, filtered on the page by
    # its category buttons (Web Development, Software Development...) or by reading.
    "unstop.com": ("internships, filter by category on the page", "https://unstop.com/internships"),
}


def listing_page(host: str | None) -> str | None:
    """A known site's listings page that needs no words (Unstop), or None."""
    key = known(host)
    if not key:
        return None
    template = SEARCH_PAGES[key][1]
    return None if "{" in template else template


def known(host: str | None) -> str | None:
    """The SEARCH_PAGES key for a host (www., in., subdomains), or None."""
    host = (host or "").casefold().strip(".")
    return next((k for k in SEARCH_PAGES if host == k or host.endswith("." + k)), None)


def dashed(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


def search_url(site: str, words: str, place: str = "") -> str | None:
    """The results page for `words` (and `place`) on a known site, or None."""
    key = known(urlsplit(site).hostname if "://" in site else site)
    if not key:
        return None
    template = SEARCH_PAGES[key][1]
    if "{" not in template:
        return template  # a listings page: the same address whatever the words
    if not words.strip():
        return None
    if "{place_dash}" in template and not place.strip():
        template = template.replace("-in-{place_dash}", "")
    return template.format(
        q=quote_plus(words.strip()),
        q_dash=dashed(words),
        place=quote_plus(place.strip()),
        place_dash=dashed(place),
    )


FINDING = (
    "\nFINDING THINGS ON A SITE: a homepage rarely holds what you are looking for. Never report "
    "'nothing found' from a front page. Search: open the site's search-results page for your "
    "words when you know its address, or use its search box (browser.fill the search field with "
    "short keywords, then browser.press Enter, then read). Read the results with "
    "browser.read {scroll:true}, go to the next page of results if there is one, and open the "
    "promising items to check their details. Report what the results show, item by item, with "
    "their links. Only when a real search shows no results say so, naming the search you made. "
    "FIND ON THE PAGE: to jump to something on a long page (a stipend, a deadline, 'Apply', "
    "a name) use browser.read {find:'stipend'}: it returns only the matches, each with the "
    "text around it and the ref to act on, instead of the whole page. "
    "WEB SEARCH SHORTCUT: when browser.search is allowed, a search for the role on the "
    "sites (for example 'frontend developer intern site:unstop.com' or 'react internship "
    "site:linkedin.com/jobs') links straight to listing pages: open the best results and "
    "read them. It works when a site's own search is hard to use. "
    "A LISTING CARD IS EVIDENCE: a result card on a search or listings page (its title, "
    "company, place, stipend and link) is enough to report an item, quoting the card's text; "
    "open the detail page only when the card lacks something you must check. "
    "SEVERAL SITES: when the job names several sites, look at EVERY one (its search or "
    "listings page) before going deeper into any, and report each site's findings. "
    "GOOD ENOUGH BEATS PERFECT: search at most twice per site. When results are broader than "
    "asked (another city, extra roles), do not search again: filter them yourself while reading "
    "(check each item's place, skills and dates) and report the ones that match. Never invent "
    "a search address beyond the known pattern. With one step left, stop and report what you "
    "found so far."
)


def guide(hosts) -> str:
    """The finding rule, plus how to build the search page of each known site among `hosts`."""
    lines = []
    for host in sorted({known(h) for h in hosts if known(h)}):
        what, template = SEARCH_PAGES[host]
        if "{" in template:
            example = search_url(host, "frontend developer intern", "Hyderabad")
            lines.append(f"{host} ({what}): {template} e.g. {example}")
        else:
            lines.append(f"{host} ({what}): {template}")
    if not lines:
        return FINDING
    return (
        FINDING
        + "\nKNOWN SEARCH PAGES (browser.open the address with your words; {q} uses + between "
        "words, {q_dash} and {place_dash} use - between words; leave out the place part if "
        "there is no place): " + " | ".join(lines)
    )
