"""Bound source inputs fairly without mutating the shared resource cache."""

import hashlib
import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field


class SourceSelection(BaseModel):
    """A reference to fetched data, never model-invented source metadata."""

    model_config = ConfigDict(extra="forbid")
    source_ref: str = Field(min_length=1, max_length=64)


def referenced_item(key, item):
    value = deepcopy(item)
    identity = [key, item.get("id"), item.get("url") or item.get("html_url"), item.get("title")]
    value["source_ref"] = (
        "src_"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()[:24]
    )
    return value


def resolve_source_items(snapshots, selections):
    """Resolve connector records; authorization is rechecked by the final verifier."""
    from mavo.outcomes import SourceEvidence
    from mavo.resources.feeds import safe_link

    catalog = {
        item["source_ref"]: item
        for snapshot in snapshots.values()
        if isinstance(snapshot, dict) and not snapshot.get("error")
        for item in snapshot.get("items", [])
        if isinstance(item, dict) and item.get("source_ref")
    }
    found, rejected = [], 0
    for selection in selections:
        item = catalog.get(selection.source_ref)
        if item is None:
            rejected += 1
            continue
        url = item.get("url") or item.get("html_url") or item.get("feed_url")
        if not isinstance(url, str) or not safe_link(url):
            rejected += 1
            continue
        title = next(
            (
                item[key]
                for key in ("title", "name", "full_name", "subject", "summary")
                if isinstance(item.get(key), str) and item[key]
            ),
            "Source record",
        )
        quote = "\n".join(source_strings(item))[:1500]
        try:
            claim = SourceEvidence(
                kind="source",
                url=url,
                quote=quote,
                title=title[:300],
                source_ref=selection.source_ref,
            )
        except ValueError:
            rejected += 1
            continue
        if not any(e.source_ref == claim.source_ref for e in found):
            found.append(claim)
    if selections and not found:
        raise FeedEvidenceError(
            "No selected source_ref matched a usable record in this assignment.",
            {"selected_items": len(selections), "unknown_references": rejected},
        )
    return found, rejected


class FeedEvidenceError(ValueError):
    """The model did not ground its digest in the supplied new feed items."""

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def source_strings(value):
    """Search only observed string values, never inferred text or metadata keys."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in source_strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in source_strings(item)]
    return []


def feed_matches(snapshots, claim):
    """A quotation and URL must belong to the SAME supplied feed item."""
    for key, snapshot in snapshots.items():
        if not key.startswith("feeds:") or snapshot.get("error"):
            continue
        for item in snapshot.get("items", []):
            if not isinstance(item, dict):
                continue
            if getattr(claim, "source_ref", None) and claim.source_ref != item.get("source_ref"):
                continue
            text = "\n".join(source_strings(item))
            if (
                claim.quote in text
                and claim.url
                == (
                    item.get("url")
                    or (item.get("feed_url") if getattr(claim, "source_ref", None) else None)
                )
                and (not claim.listing_id or claim.listing_id in text)
            ):
                yield item


def bind_feed_citations(snapshots, checkpoint):
    """Recover a copied headline's link from input, never invent or fetch evidence.

    Models sometimes strip a feed link's query or rewrite its path. A verbatim
    headline quotation (at least the schema's 24 characters) identifying ONE
    observed URL references that input item. Use the observed title/link, never
    model-reconstructed metadata. Reject ambiguous quotations and URLs belonging
    to another supplied item. Never bind listing/action evidence.
    """
    items = [
        item
        for key, snap in snapshots.items()
        if key.startswith("feeds:") and not snap.get("error")
        for item in snap.get("items", [])
    ]
    urls = {item.get("url") for item in items}
    repaired = 0
    for index, claim in enumerate(checkpoint.evidence):
        if claim.kind != "source" or list(feed_matches(snapshots, claim)) or claim.url in urls:
            continue
        candidates = {
            item["url"]: item
            for item in items
            if claim.quote in item.get("title", "") and item.get("url") and not claim.listing_id
        }
        if len(candidates) != 1:
            continue
        url, item = candidates.popitem()
        fixed = type(claim).model_validate(
            {
                **claim.model_dump(),
                "url": url,
                "title": item["title"][:300] if claim.title else "",
            }
        )
        checkpoint.evidence[index] = fixed
        # Do not leave the incorrect cited link in user-facing text.
        checkpoint.summary = checkpoint.summary.replace(claim.url, url)
        if checkpoint.notification:
            checkpoint.notification = checkpoint.notification.replace(claim.url, url)
        repaired += 1
    if repaired:
        # Input binding cannot bypass the checkpoint's existing size/type limits.
        type(checkpoint).model_validate(checkpoint.model_dump())
    return repaired


def validate_feed_checkpoint(snapshots, checkpoint):
    """Bounded repair hint, not authorization or a replacement for final verification."""
    if (
        not snapshots
        or any(not key.startswith("feeds:") for key in snapshots)
        or not any(s.get("items") for s in snapshots.values() if not s.get("error"))
        or checkpoint.actions
        or checkpoint.lookups
        or checkpoint.state == "continue"
    ):
        return {}
    from mavo.outcomes import SourceEvidence
    from mavo.resources.feeds import safe_link

    repaired = bind_feed_citations(snapshots, checkpoint)
    catalog = {
        item["source_ref"]: (key, item)
        for key, snapshot in snapshots.items()
        if not snapshot.get("error")
        for item in snapshot.get("items", [])
        if isinstance(item, dict) and item.get("source_ref")
    }
    by_url, accepted, rejected_refs = {}, [], []
    for selection in checkpoint.source_items:
        found = catalog.get(selection.source_ref)
        if not found:
            rejected_refs.append(selection.source_ref)
            continue
        key, item = found
        title, url = item.get("title"), item.get("url") or item.get("feed_url")
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(url, str)
            or not safe_link(url)
        ):
            rejected_refs.append(selection.source_ref)
            continue
        quote = title if len(title) >= 24 else "\n".join(source_strings(item))
        try:
            evidence = SourceEvidence(
                kind="source",
                url=url,
                quote=quote[:1500],
                title=title[:300],
                source_ref=selection.source_ref,
            )
        except ValueError:
            rejected_refs.append(selection.source_ref)
            continue
        record_key = (url, selection.source_ref if not item.get("url") else "")
        if record_key not in by_url:
            accepted.append(evidence)
            by_url[record_key] = (key, item)
    unmatched = []
    for index, claim in enumerate(checkpoint.evidence):
        matches = list(feed_matches(snapshots, claim)) if claim.kind == "source" else []
        if not matches:
            unmatched.append(index)
            continue
        item = matches[0]
        record_key = (claim.url, claim.source_ref if not item.get("url") else "")
        if record_key not in by_url:
            key = next(
                key for key, snapshot in snapshots.items() if item in snapshot.get("items", [])
            )
            accepted.append(claim)
            by_url[record_key] = (key, item)
    diagnostics = {
        "evidence_count": len(checkpoint.evidence),
        "bound_links": repaired,
        "unmatched_entries": unmatched,
        "selected_items": len(checkpoint.source_items),
        "unknown_references": len(rejected_refs),
        "accepted_items": min(len(accepted), 8),
        "partial": bool(unmatched or rejected_refs),
    }
    if not accepted:
        raise FeedEvidenceError(
            "Available source items are already deduplicated by Mavo. Select their "
            "source_ref values in source_items; do not invent URLs, request previous "
            "history, or declare no new items. "
            f"Unmatched evidence indexes: {diagnostics['unmatched_entries']}.",
            diagnostics,
        )
    checkpoint.evidence = []
    lines = []
    from mavo.resources.feed_catalog import FEEDS

    for evidence in accepted[:8]:
        item = next(feed_matches(snapshots, evidence))
        key = next(key for key, snapshot in snapshots.items() if item in snapshot.get("items", []))
        title = " ".join(item.get("title", "Untitled").split())[:300]
        source = key.removeprefix("feeds:")
        label = FEEDS[source][0] if source in FEEDS else source.replace("-", " ")
        url = evidence.url.replace("(", "%28").replace(")", "%29")
        link_label = "Source" if item.get("url") else "Publisher feed"
        line = f"- {title} — {label} · [{link_label}]({url})"
        if sum(len(value) + 1 for value in lines) + len(line) > 11000:
            continue
        lines.append(line)
        checkpoint.evidence.append(evidence)
    diagnostics["accepted_items"] = len(checkpoint.evidence)
    diagnostics["deferred_items"] = len(accepted) - len(checkpoint.evidence)
    if diagnostics["partial"]:
        lines.append("\nPartial digest: unsupported model results were omitted.")
    if diagnostics["deferred_items"]:
        lines.append("\nMore selected stories remain for a later review.")
    # Only accepted source records enter the delivered result. Never retain a
    # rejected story in model prose, a notification, or a claimed next action.
    checkpoint.summary = "\n".join(lines)
    checkpoint.notification = None
    checkpoint.question = None
    checkpoint.next_step = ""
    checkpoint.state = "wait_for_next_review"
    return diagnostics


def fallback_feed_headlines(snapshots, checkpoint):
    """Degrade a failed RSS selection to observed headlines, never invented analysis.

    The caller must limit this to a feed-only, non-review assignment after its
    bounded repair. Normal receipts, authorization, outcome checks and outbox
    deduplication still apply. This is not a fallback for tools or other resources.
    """
    if (
        not snapshots
        or any(not key.startswith("feeds:") for key in snapshots)
        or checkpoint.actions
        or checkpoint.lookups
        or checkpoint.state == "continue"
        or checkpoint.review_verdict is not None
    ):
        raise FeedEvidenceError("A headline fallback cannot satisfy this assignment.")
    from mavo.resources.feeds import safe_link

    groups = []
    for snapshot in snapshots.values():
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("error")
            or snapshot.get("stale")
            or snapshot.get("status") in {"stale", "error", "failed", "unavailable", "disconnected"}
        ):
            continue
        groups.append(
            [
                item
                for item in snapshot.get("items", [])
                if isinstance(item, dict)
                and item.get("source_ref")
                and isinstance(item.get("title"), str)
                and item["title"].strip()
                and isinstance(item.get("url") or item.get("feed_url"), str)
                and safe_link(item.get("url") or item.get("feed_url"))
            ]
        )
    # Round-robin the already bounded, deduplicated source inputs. A large feed
    # must not starve the others. Original publisher order is retained per feed.
    selected, seen = [], set()
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index >= len(group):
                continue
            item = group[index]
            key = item.get("url") or item["source_ref"]
            if key not in seen:
                selected.append(SourceSelection(source_ref=item["source_ref"]))
                seen.add(key)
    if not selected:
        raise FeedEvidenceError("No current usable feed headlines are available.")
    # Discard all model-written prose and claims, including invented notifications.
    replacement = type(checkpoint)(state="wait_for_next_review", source_items=selected[:12])
    diagnostics = validate_feed_checkpoint(snapshots, replacement)
    if not diagnostics.get("accepted_items"):
        raise FeedEvidenceError("No current usable feed headlines are available.")
    replacement.summary = (
        "Feed headlines — AI selection was unavailable. These are new fetched headlines, "
        "not an AI-ranked or analyzed digest.\n\n" + replacement.summary
    )
    diagnostics.update(fallback="feed_headlines", partial=True)
    diagnostics["deferred_items"] = max(
        diagnostics.get("deferred_items", 0), len(selected) - len(replacement.evidence)
    )
    unavailable = sum(
        bool(
            s.get("error")
            or s.get("stale")
            or s.get("status") in {"stale", "error", "failed", "unavailable", "disconnected"}
        )
        for s in snapshots.values()
        if isinstance(s, dict)
    )
    if unavailable:
        replacement.summary += f"\n\n{unavailable} feed source(s) were unavailable."
    return replacement, diagnostics


def feed_evidence_example(snapshots):
    """One real, bounded citation shape to remove URL/quote copying ambiguity."""
    for key, snapshot in snapshots.items():
        if not key.startswith("feeds:") or snapshot.get("error"):
            continue
        for item in snapshot.get("items", []):
            title, url = item.get("title", ""), item.get("url", "")
            if len(title) >= 24 and url.startswith(("http://", "https://")):
                return {"kind": "source", "quote": title[:160], "url": url}
    return None


def bounded_snapshots(snapshots, limit=18000):
    result = {}
    allowance = max(256, (limit - 2) // max(1, len(snapshots)) - 100)
    for key, snapshot in snapshots.items():
        value = (
            deepcopy(snapshot)
            if isinstance(snapshot, dict)
            else {"error": "Resource returned an invalid snapshot", "status": "unavailable"}
        )
        raw_items = value.pop("items", [])
        if value.get("status") is not None and not isinstance(value["status"], str):
            value["error"] = "Resource supplied an invalid status"
        if (
            value.get("status") in ("unavailable", "failed", "error", "disconnected", "stale")
            or value.get("stale") is True
        ):
            value.setdefault("error", "Resource is unavailable or stale")
        if not isinstance(raw_items, list):
            value.update(error="Resource returned an invalid item list", status="unavailable")
            raw_items = []
        invalid = sum(not isinstance(item, dict) for item in raw_items)
        items = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if key.startswith("feeds:"):
                from mavo.resources.feed_catalog import FEEDS
                from mavo.resources.feeds import safe_link

                if (
                    not isinstance(item.get("title"), str)
                    or not item["title"].strip()
                    or (
                        item.get("url") not in (None, "")
                        and (not isinstance(item["url"], str) or not safe_link(item["url"]))
                    )
                ):
                    invalid += 1
                    continue
                if not item.get("url"):
                    source = key.removeprefix("feeds:")
                    if source not in FEEDS:
                        invalid += 1
                        continue
                    item = {**item, "feed_url": FEEDS[source][1]}
            items.append(referenced_item(key, item))
        value["invalid_item_count"] = invalid
        if raw_items and not items:
            value.update(error="Resource supplied no usable records", status="unavailable")
        value["items"] = []
        value["available_item_count"] = len(items)
        value["items_omitted"] = len(items)
        for item in items:
            candidate = {**value, "items": [*value["items"], item]}
            if len(json.dumps(candidate)) > allowance:
                continue
            value["items"].append(item)
        value["items_omitted"] = len(items) - len(value["items"])
        result[key] = value
    if len(json.dumps(result)) > limit:
        raise ValueError("Source metadata exceeds the input budget; select fewer sources")
    return result
