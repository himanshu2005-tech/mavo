"""Deterministic reproduction of the live 4/5-correct citation failure. No credits."""

from copy import deepcopy

import pytest

from mavo.crew_workers import WorkCheckpoint
from mavo.source_snapshots import FeedEvidenceError, feed_evidence_example, validate_feed_checkpoint

TITLE = "Researchers publish an open source assistant toolkit for students"


def report(items, evidence):
    return {"feeds:bbc-tech": {"items": items}}, WorkCheckpoint(
        state="wait_for_next_review",
        summary="News https://example.org/rewritten",
        notification="Read https://example.org/rewritten",
        evidence=evidence,
    )


def claim(url="https://example.org/rewritten", quote=TITLE, **extra):
    return {"kind": "source", "url": url, "quote": quote, **extra}


def test_exact_unique_headline_binds_the_actual_supplied_url_without_paid_repair():
    original = {"id": "real", "title": TITLE, "url": "https://example.org/real?tracking=rss"}
    snapshots, checkpoint = report([original], [claim()])
    before = deepcopy(snapshots)
    diagnostics = validate_feed_checkpoint(snapshots, checkpoint)
    assert diagnostics["bound_links"] == 1 and diagnostics["unmatched_entries"] == []
    assert checkpoint.evidence[0].url == original["url"]
    assert original["url"] in checkpoint.summary and checkpoint.notification is None
    assert "rewritten" not in checkpoint.summary
    assert snapshots == before


def test_four_correct_citations_and_one_rewritten_link_do_not_fail_the_entire_digest():
    items = [
        {"id": str(i), "title": TITLE + str(i), "url": f"https://example.org/{i}"} for i in range(5)
    ]
    evidence = [claim(url=item["url"], quote=item["title"]) for item in items]
    evidence[2]["url"] = "https://example.org/rewritten"
    snapshots, checkpoint = report(items, evidence)
    assert validate_feed_checkpoint(snapshots, checkpoint)["bound_links"] == 1
    assert [e.url for e in checkpoint.evidence] == [i["url"] for i in items]


@pytest.mark.parametrize(
    "variant", ["invented", "partial", "ambiguous", "cross_item", "listing", "wrong_title"]
)
def test_binding_cannot_launder_invented_ambiguous_or_cross_item_evidence(variant):
    items = [{"id": "real", "title": TITLE, "url": "https://example.org/real"}]
    evidence = claim()
    if variant == "invented":
        evidence["quote"] = "An invented news headline never read from an authorized source"
    elif variant == "partial":
        evidence["quote"] = TITLE[:30]
        items.append(
            {"title": TITLE[:30] + " in another story", "url": "https://example.org/other"}
        )
    elif variant == "ambiguous":
        items.append({"title": TITLE, "url": "https://example.org/different"})
    elif variant == "cross_item":
        items.append({"title": "A totally unrelated observed story", "url": evidence["url"]})
    elif variant == "listing":
        evidence["kind"] = "listing"
    else:
        evidence["title"] = "An invented title"
        evidence["quote"] = "An invented quotation which is not present in the supplied feed"
    snapshots, checkpoint = report(items, [evidence])
    with pytest.raises(FeedEvidenceError) as error:
        validate_feed_checkpoint(snapshots, checkpoint)
    assert error.value.diagnostics["unmatched_entries"] == [0]
    assert checkpoint.evidence[0].url == "https://example.org/rewritten"


def test_unique_verbatim_headline_fragment_uses_observed_title_not_model_title():
    item = {"title": TITLE, "url": "https://example.org/actual?rss=1"}
    snapshots, checkpoint = report([item], [claim(quote=TITLE[:39], title="Model shortened title")])
    assert validate_feed_checkpoint(snapshots, checkpoint)["bound_links"] == 1
    assert checkpoint.evidence[0].title == TITLE
    assert checkpoint.evidence[0].url == item["url"]
    assert checkpoint.evidence[0].quote == TITLE[:39]


def test_duplicate_feeds_of_same_article_are_not_ambiguous_but_unknown_receipts_are():
    item = {"title": TITLE, "url": "https://example.org/real"}
    snapshots, checkpoint = report([item, deepcopy(item)], [claim()])
    assert validate_feed_checkpoint(snapshots, checkpoint)["bound_links"] == 1
    snapshots["feeds:bbc-tech"]["error"] = "Unavailable"
    _, new = report([], [claim()])
    assert validate_feed_checkpoint(snapshots, new) == {}
    assert new.evidence[0].url.endswith("rewritten")


def test_no_new_items_and_mixed_tool_work_are_not_rewritten():
    snapshots, checkpoint = report([], [])
    assert validate_feed_checkpoint(snapshots, checkpoint) == {}
    snapshots["google:gmail"] = {"items": []}
    assert validate_feed_checkpoint(snapshots, checkpoint) == {}


def test_citation_example_is_observed_source_data_not_an_invented_default():
    assert feed_evidence_example({"feeds:bbc-tech": {"error": "Unavailable", "items": []}}) is None
    snapshots = {"feeds:bbc-tech": {"items": [{"title": TITLE, "url": "https://example.org/real"}]}}
    example = feed_evidence_example(snapshots)
    assert example == {"kind": "source", "quote": TITLE, "url": "https://example.org/real"}
    snapshots["feeds:bbc-tech"]["items"][0]["url"] = "javascript:alert(1)"
    assert feed_evidence_example(snapshots) is None
