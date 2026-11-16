"""A browser step as a card in "What it's thinking": what it did, on which page, and how it went
(opened, read, clicked; refused or failed, with why). The same cards the meeting agent shows."""

from urllib.parse import urlsplit

BROWSER_CARDS = {
    "browser.open",
    "browser.navigate",
    "browser.read",
    "browser.search",
    "browser.click",
    "browser.fill",
    "browser.select",
    "browser.press",
}


def page_of(url: str) -> str:
    """ "airouter.in/dashboard/billing": a page as a person reads its address."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").removeprefix("www.")
    return (host + parts.path.rstrip("/"))[:120] if host else ""


def card_of(operation: str, arguments: dict | None, outcome: dict) -> dict | None:
    """The card for one finished browser step, or None for a step not worth a card."""
    if operation not in BROWSER_CARDS:
        return None
    args = arguments or {}
    result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    status = outcome.get("status")
    state = (
        "done"
        if status in {"completed", "previewed"}
        else "waiting"
        if status == "pending"
        else "failed"
    )
    page = page_of(result.get("url") or args.get("url") or "")
    title = str(result.get("title") or "").strip()[:100]
    if operation == "browser.open":
        about = page or str(args.get("url") or "")[:120]
        said = title
    elif operation == "browser.navigate":
        about = str(args.get("goal") or "")[:100]
        reached = (result.get("navigation") or {}) if isinstance(result, dict) else {}
        said = (
            f"Not found: {reached.get('note')}"[:200]
            if isinstance(reached, dict) and reached.get("reached") is False
            else page
        )
    elif operation == "browser.read":
        about = title or page
        find = result.get("find") if isinstance(result, dict) else None
        said = (
            f'Looked for "{find.get("text")}": {len(find.get("matches") or [])} found'
            if isinstance(find, dict)
            else page
        )
    elif operation == "browser.search":
        about = str(args.get("query") or "")[:120]
        said = f"{len(result.get('results') or [])} results" if isinstance(result, dict) else ""
    else:
        about = str(outcome.get("target_summary") or args.get("ref") or "")[:120]
        said = page
    wall = result.get("sign_in_required") if isinstance(result, dict) else None
    if wall and state == "done":
        state, said = "asked", f"{wall.get('host') or 'The site'} may need you to sign in"
    if state == "failed":
        said = str(outcome.get("error") or "It did not work.")[:200]
    return {
        "label": f"{operation.split('.')[1].replace('_', ' ')} {page}".strip()[:120],
        "text": said,
        "tool": operation,
        "outcome": state,
        "about": about,
        "result": said,
    }
