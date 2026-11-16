"""Evidence checks are application decisions, not a model's self-reported success."""

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from mavo.database import ActionRequest, CrewInstance
from mavo.source_snapshots import feed_matches, source_strings


class SourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["listing", "zero_results", "source"]
    url: str = Field(pattern=r"^https?://", max_length=2048)
    quote: str = Field(min_length=24, max_length=1500)
    title: str = Field(default="", max_length=300)
    listing_id: str | None = Field(default=None, max_length=255)
    source_ref: str | None = Field(default=None, max_length=64)


DASHES = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "‒": "-",
        "−": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        " ": " ",
        "₹": "rs ",
    }
)


def plain_text(value: str) -> str:
    """Text as compared: capitals, spacing, dashes and quote marks do not count."""
    return " ".join(str(value).translate(DASHES).casefold().split())


def quoted_in(quote: str, text: str) -> bool:
    """Is the quote on the page? Exactly after plain_text, or, when the agent joined pieces of
    a card with "...", every piece in order. (A model's quote that differed by a space or a
    dash used to fail the whole report as "no source evidence".)"""
    page = plain_text(text)
    pieces = [p for p in (plain_text(x) for x in re.split(r"\.\.\.|…", quote)) if p]
    if not pieces:
        return False
    at = 0
    for piece in pieces:
        found = page.find(piece, at)
        if found < 0:
            return False
        at = found + len(piece)
    return True


def plain_url(url: str) -> str:
    """A link as compared: http or https, www., a trailing slash and tracking tags do not count."""
    from urllib.parse import parse_qsl, urlencode, urlsplit

    parts = urlsplit(str(url).strip().rstrip(".,;)]}"))
    host = (parts.hostname or "").casefold().removeprefix("www.")
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query)
            if not k.casefold().startswith(("utm_", "ref", "trk"))
        ]
    )
    return host + parts.path.rstrip("/") + ("?" + query if query else "")


def seen_record(value):
    """(text, links, source_ref) of something the agent saw. A page's short links ("-> /internships
    /x") count as full links on that page's site: agents report the full link, and a correct
    listing was rejected because the page only showed the short one."""
    from urllib.parse import urljoin

    values = source_strings(value)
    urls = set()
    base = value.get("url") if isinstance(value, dict) and isinstance(value.get("url"), str) else ""
    for text in values:
        if text.startswith(("https://", "http://")) and not any(c.isspace() for c in text):
            urls.add(text)
        urls.update(url.rstrip(".,;)]}") for url in re.findall(r'https?://[^\s<>"\']+', text))
        if base.startswith(("https://", "http://")):
            urls.update(
                urljoin(base, path.rstrip(".,;)]}"))
                for path in re.findall(r'(?:→|->|href=)\s*"?(/[^\s<>"\']*)', text)
            )
    return (
        "\n".join(values),
        urls,
        value.get("source_ref") if isinstance(value, dict) else None,
    )


def derive_evidence(session, worker_id, since, report: str) -> list[dict]:
    """Evidence built by Mavo from what the agent saw, for a report that gives links but no
    evidence list (a small model forgets it): each reported link that is on a page the agent
    read, with that page's own text for the item. Nothing the agent did not see gets in."""
    reads = session.scalars(
        select(ActionRequest).where(
            ActionRequest.crew_id == worker_id,
            ActionRequest.created_at >= since,
            ActionRequest.operation.in_(["browser.read", "browser.search", "browser.open"]),
            ActionRequest.status == "completed",
        )
    ).all()
    from urllib.parse import urlsplit

    seen = [(seen_record(r.result or {}), r.result or {}) for r in reads]
    out, used = [], set()
    for url in re.findall(r'https?://[^\s<>"\')\]]+', report or ""):
        url = url.rstrip(".,;)]}*")
        key = plain_url(url)
        path = urlsplit(url).path.rstrip("/")
        if key in used or len(path) < 2:
            continue
        for (text, urls, _), raw in seen:
            if key not in {plain_url(u) for u in urls}:
                continue
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            title, at = None, None
            if isinstance(raw, dict) and plain_url(raw.get("url") or "") == key:
                # The item's own page: its title.
                title = str(raw.get("title") or "").split(" | ")[0].strip()
            else:
                # The link on a results page: [e12] link "AI & React Intern" -> /internships/x
                for line in lines:
                    if path in line or url in line:
                        named = re.search(r'"([^"]{6,200})"', line)
                        title = named.group(1) if named else None
                        break
            if not title or len(title) < 6:
                continue
            # The card's own text, after the link line when the card repeats the title.
            at = next(
                (
                    i
                    for i, x in enumerate(lines)
                    if plain_text(x).startswith(plain_text(title)) and "→" not in x
                ),
                None,
            )
            if at is None:
                at = next(
                    (i for i, x in enumerate(lines) if plain_text(title) in plain_text(x)), None
                )
            if at is None:
                continue
            quote = " ".join(lines[at : at + 3])[:1400]
            if len(quote) < 24 or plain_text(title) not in plain_text(quote):
                continue
            out.append({"kind": "listing", "url": url, "title": title[:300], "quote": quote})
            used.add(key)
            break
    return out[:12]


VALUE = re.compile(r"(?<![\w.])[$₹€£]?\d[\d,]*(?:\.\d+)?%?(?![\w])")
SAID = re.compile(r"[\"“']([^\"”']{4,120})[\"”']")


def values_in(report: str) -> list[str]:
    """What a report states that a page can show word for word: its numbers ("1,240",
    "$12.50", "85%") and anything it quotes. A bare number under three digits says too little
    to tie a report to a page, so it does not count alone."""
    found = [
        v
        for v in VALUE.findall(report or "")
        if len(re.sub(r"\D", "", v)) >= 3 or re.search(r"[$₹€£%.]", v)
    ]
    found += [s.strip() for s in SAID.findall(report or "") if s.strip()]
    return list(dict.fromkeys(found))[:12]


def ground_evidence(session, worker_id, since, report: str) -> list[dict]:
    """Evidence made by Mavo, not copied by the model: each value the report states, found on a
    page the agent read, quoted with that page's own line (and the line before it, where a label
    such as "Credits remaining" sits over its number). A short answer ("Credits: 1,240") failed
    the whole run because its quote was under 24 characters; nothing the agent did not see gets
    in, since every quote is the page's own text."""
    reads = session.scalars(
        select(ActionRequest)
        .where(
            ActionRequest.crew_id == worker_id,
            ActionRequest.created_at >= since,
            ActionRequest.operation == "browser.read",
            ActionRequest.status == "completed",
        )
        .order_by(ActionRequest.created_at.desc())
    ).all()
    pages = [
        (str(r.result.get("url")), str(r.result.get("text") or ""))
        for r in reads
        if isinstance(r.result, dict) and str(r.result.get("url") or "").startswith("http")
    ]
    out, used = [], set()
    for value in values_in(report):
        want = plain_text(value)
        for url, text in pages:
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            at = next((i for i, x in enumerate(lines) if want in plain_text(x)), None)
            if at is None:
                continue
            start, end = at, at + 1
            quote = " ".join(lines[start:end])
            while len(quote) < 24 and (start > 0 or end < len(lines)):
                start, end = max(0, start - 1), min(len(lines), end + (start == 0))
                quote = " ".join(lines[start:end])
            quote = quote[:1400]
            if len(quote) < 24 or (url, quote) in used:
                break
            used.add((url, quote))
            out.append({"kind": "source", "url": url, "quote": quote})
            break
    return out[:12]


def verify_sources(session, worker_id, since, evidence, *, require_listings=False):
    worker = session.get(CrewInstance, worker_id)
    # What the agent saw: pages it read, and the results of web searches and pages it opened
    # (a search for "role site:unstop.com" lists the listing pages themselves).
    reads = session.scalars(
        select(ActionRequest).where(
            ActionRequest.crew_id == worker_id,
            ActionRequest.created_at >= since,
            ActionRequest.operation.in_(["browser.read", "browser.search", "browser.open"]),
            ActionRequest.status == "completed",
        )
    ).all()

    record = seen_record

    sources = (
        [record(r.result or {}) for r in reads]
        if worker and "action:browser.read" in worker.permissions
        else []
    )
    authorized_snapshots = []
    unavailable_sources = []
    if worker:
        from mavo.lookup_progress import scope_for

        scope = scope_for(worker)
        saved = worker.observation.get("source_receipts", {})
        if saved.get("scope") == scope:
            for key, snapshot in saved.get("snapshots", {}).items():
                if not isinstance(snapshot, dict):
                    if key + ":read" in worker.permissions:
                        unavailable_sources.append(key)
                    continue
                failed = bool(
                    snapshot.get("error")
                    or snapshot.get("status")
                    in ("unavailable", "failed", "error", "disconnected", "stale")
                    or snapshot.get("stale") is True
                    or ("items" in snapshot and not isinstance(snapshot["items"], list))
                )
                if key + ":read" in worker.permissions and failed:
                    unavailable_sources.append(key)
                if key + ":read" in worker.permissions and not failed:
                    if not key.startswith("feeds:"):
                        items = snapshot.get("items")
                        if isinstance(items, list):
                            sources.extend(record(item) for item in items if isinstance(item, dict))
                        else:
                            sources.append(record(snapshot))
                    authorized_snapshots.append((key, snapshot))
        saved = worker.observation.get("lookup_progress", {})
        from mavo.resource_pool import level

        if saved.get("scope") == scope and level(session, "open_data") != "off":
            sources.extend(
                record(r["response"])
                for r in saved.get("receipts", [])
                if r["response"].get("status") == "ok"
            )
    confirmed = []
    source_item_ids = []
    source_urls = []
    rejected = 0
    for item in evidence:
        try:
            claim = SourceEvidence.model_validate(item)
        except ValueError:
            rejected += 1
            continue
        if require_listings and claim.kind == "source":
            rejected += 1
            continue
        if (
            require_listings
            and claim.kind == "zero_results"
            and not any(
                marker in claim.quote.casefold()
                for marker in (
                    "no matching jobs",
                    "no jobs found",
                    "no results",
                    "0 results",
                    "no openings",
                )
            )
        ):
            rejected += 1
            continue
        matched_items = list(feed_matches(dict(authorized_snapshots), claim))
        claimed_url = plain_url(claim.url)
        if matched_items or any(
            quoted_in(claim.quote, text)
            and claimed_url in {plain_url(u) for u in urls}
            and (not claim.source_ref or claim.source_ref == ref)
            and (not claim.listing_id or claim.listing_id in text)
            for text, urls, ref in sources
        ):
            if claim.kind != "listing" or (
                claim.title and plain_text(claim.title) in plain_text(claim.quote)
            ):
                value = claim.model_dump()
                identity = (
                    claim.url
                    + (
                        "\n" + (claim.source_ref or "")
                        if matched_items and not any(item.get("url") for item in matched_items)
                        else ""
                    )
                    if matched_items
                    else (claim.url + "\n" + (claim.listing_id or claim.title + "\n" + claim.quote))
                )
                value["evidence_id"] = hashlib.sha256(identity.encode()).hexdigest()
                confirmed.append(value)
                if any(item.get("url") for item in matched_items):
                    source_urls.append(claim.url)
                for matched in matched_items:
                    key = matched.get("id") or matched.get("url") or matched.get("title")
                    if key:
                        source_item_ids.append(str(key)[:300])
            else:
                rejected += 1
        else:
            rejected += 1
    quiet = bool(
        not require_listings
        and not evidence
        and authorized_snapshots
        and worker
        and all(
            cap in {"model:invoke", "notifications:show"} or cap.endswith((":read", ":body"))
            for cap in worker.permissions
        )
        and all(
            isinstance(snapshot, dict)
            and snapshot.get("items") == []
            and not snapshot.get("items_omitted", 0)
            and not snapshot.get("invalid_item_count", 0)
            for key, snapshot in authorized_snapshots
        )
    )
    return {
        "operation": "succeeded" if sources or authorized_snapshots else "not_observed",
        "step": "evidence_checked" if confirmed or quiet else "unverified",
        "quiet": quiet,
        "unavailable_sources": unavailable_sources,
        "objective": "requires_review",
        "evidence": confirmed,
        "rejected_evidence_count": rejected,
        "partial": bool(
            rejected
            or unavailable_sources
            or any(snapshot.get("invalid_item_count", 0) for _, snapshot in authorized_snapshots)
        ),
        "source_item_ids": list(dict.fromkeys(source_item_ids)),
        "source_urls": list(dict.fromkeys(source_urls)),
        "reason": "Authorized sources returned no new items."
        if quiet
        else "Source quotations matched authorized source reads."
        if confirmed
        else "Connected sources are unavailable or stale; no current report could be verified."
        if unavailable_sources and not authorized_snapshots and not sources
        else "No source evidence matched an authorized source read in this assignment.",
    }
