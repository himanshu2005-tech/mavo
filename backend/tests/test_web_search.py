"""Web search, fine-tuned: current pages for current questions, more than one phrasing, several
sites rather than one site's pages, and an answer that cites what it read."""

import asyncio
import json

from mavo.desktop_actions import BrowserSearch, search_url
from mavo.site_skills import WebParams, recency, web_read

SENTENCE = "A full sentence about the thing that was asked, long enough to count as prose here. "


class Searcher:
    """A browser whose searches answer by phrasing, and remember what was asked."""

    def __init__(self, answers):
        self.answers, self.searched, self.page = answers, [], self

    def check(self):
        pass

    async def browser(self, operation, args, rule, owner, scope_guard=None, limit=None):
        if operation == "browser.search":
            self.searched.append((args["query"], args.get("recent")))
            return {"results": self.answers.get((args["query"], args.get("recent")), [])}
        return {"url": args["url"]}


def result(url):
    return {"title": url, "url": url, "snippet": "s"}


def run(desktop, monkeypatch, **params):
    async def quick(urls):
        return {u: SENTENCE * 8 for u in urls}

    monkeypatch.setattr("mavo.site_skills.fetch_pages", quick)
    rule = {"mode": "unrestricted", "domains": []}
    return json.loads(asyncio.run(web_read(desktop, rule, "o", WebParams(**params))))


def test_current_questions_search_recent_pages():
    assert recency("iphone 17 price india") == "week"
    assert recency("what happened today in cricket") == "day"
    assert recency("upcoming hackathons in hyderabad") == "month"
    assert recency("history of rome") is None
    assert search_url("price", "week").endswith("&df=w")
    assert "df=" not in search_url("history of rome")
    assert BrowserSearch(query="x", recent="day").recent == "day"


def test_every_phrasing_is_searched_merged_and_one_site_cannot_fill_the_answer(monkeypatch):
    desktop = Searcher(
        {
            ("laptop price", "week"): [
                result("https://shop.example/a"),
                result("https://shop.example/b"),
                result("https://shop.example/c"),
                result("https://news.example/x"),
            ],
            ("laptop cost", "week"): [
                result("https://news.example/x"),  # already found: read once
                result("https://review.example/y"),
            ],
        }
    )
    out = run(desktop, monkeypatch, query="laptop price", also=["laptop cost"], pages=6)
    assert desktop.searched == [("laptop price", "week"), ("laptop cost", "week")]
    read = [p["url"] for p in out["pages_read"]]
    assert read == [
        "https://shop.example/a",
        "https://shop.example/b",
        "https://news.example/x",
        "https://review.example/y",
    ]  # at most two from shop.example, and news.example once
    assert out["searched_recent"] == "week"
    assert "[1]" in out["how_to_answer"] and "Sources" in out["how_to_answer"]


def test_nothing_recent_falls_back_to_any_date(monkeypatch):
    desktop = Searcher({("latest python release", None): [result("https://python.example/r")]})
    out = run(desktop, monkeypatch, query="latest python release")
    assert desktop.searched == [("latest python release", "week"), ("latest python release", None)]
    assert [p["url"] for p in out["pages_read"]] == ["https://python.example/r"]
