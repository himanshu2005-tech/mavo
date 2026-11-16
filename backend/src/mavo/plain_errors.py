"""What the person is told when something goes wrong: little, plainly, and only when there is
something for them to do.

A run that failed and will be tried again says nothing on the desktop. A "try later" outcome (a
site limiting requests) says nothing. Only an agent that has stopped after repeated failures, or
that needs the person, speaks, in everyday words with the one thing to do. The technical text is
kept for the log and the agent's details, never shown as the message."""

from __future__ import annotations

import re

# (pattern, what kind of trouble, the plain words). First match wins. The AI patterns follow the
# exact texts model_router raises, not loose words (an agent may be named "Credits Watch").
KINDS: list[tuple[str, str, str]] = [
    (
        r"\(402\)|out of credits|insufficient (?:credits?|balance|funds|quota)"
        r"|payment required|exceeded your [a-z ]*quota",
        "ai",
        "the AI connection is out of credits",
    ),
    (
        r"rejected the saved api key|api key was rejected|\((?:401|403)\)"
        r"|configure .{1,40} before using it",
        "ai",
        "the AI connection's key was not accepted",
    ),
    (
        r"is not reachable|could not complete the request|returned an invalid response"
        r"|returned no text|returned invalid data|returned no json",
        "ai",
        "the AI connection could not be reached",
    ),
    (r"daily model-call budget is used up", "budget_day", "today's AI calls are used up"),
    (r"took too long|timed? ?out|timeout", "slow", "the AI model took too long to answer"),
    (
        r"invalid work checkpoint|reply was not valid|left out \d+ of|did not classify"
        r"|validation error|resolve each actionable mail|did not select any",
        "answer",
        "the AI model's answer could not be used",
    ),
    (
        r"ran out of steps|budget ended|model-call budget",
        "steps",
        "it needed more steps than its limit allows",
    ),
    (
        r"limiting requests|too many requests|rate.?limit",
        "busy_site",
        "a site it reads was limiting requests",
    ),
    (
        r"google [a-z ]*(?:unavailable|failed)|gmail history",
        "google",
        "Google could not be reached",
    ),
    (
        r"browser stayed busy|waiting for the browser",
        "browser",
        "Mavo's browser was busy with another job",
    ),
    (r"needs you to sign in|sign-in|signed out", "signin", "a site needs you to sign in"),
    (
        r"lacks permission|not allowed|outside [a-z' ]* scope|restricted operations|grant ",
        "access",
        "it needs access it was not given",
    ),
]

# Results that mean "nothing to tell the person; the next run tries again".
QUIET = re.compile(r"limiting requests right now|Mavo tries again at the next run", re.I)


def kind_of(text: str | None) -> tuple[str, str]:
    """(kind, plain words) for an internal error text."""
    value = (text or "").casefold()
    for pattern, kind, words in KINDS:
        if re.search(pattern, value):
            return kind, words
    return "other", "something went wrong"


def retrying(text: str | None, minutes: int) -> str:
    """Shown on the agent's panel while it waits to try again. Never a desktop alert."""
    return f"Had a hiccup ({kind_of(text)[1]}). Trying again in {minutes} min."


def stopped(name: str, text: str | None) -> str:
    """The one alert when an agent has stopped after repeated failures."""
    kind, words = kind_of(text)
    fix = {
        "ai": "Check the connection in Settings (credits and key), then press Start.",
        "steps": "Raise its AI calls per run in its panel, then press Start.",
        "signin": "Save a login with the key button in chat, then press Start.",
        "access": "Open its panel and allow what it asks for, then press Start.",
    }.get(kind, "Press Start on its panel to try again.")
    return f"{name} is paused: {words}, three runs in a row. {fix}"


def quiet(text: str | None) -> bool:
    return bool(QUIET.search(text or ""))


# "Needs you" reasons with nothing for the person to do: they clear by themselves.
WAITS = re.compile(
    r"browser stayed busy|already executing work|did not return a pass/fix verdict"
    r"|review limit reached|daily model-call budget is used up",
    re.I,
)


def waits_itself(reason: str | None) -> bool:
    return bool(WAITS.search(reason or ""))
