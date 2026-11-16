"""Words appear as they are written: the chat's reply streams while the model writes it.

The planner answers in JSON, and only its "reply" field is for the person. While the JSON
arrives, the text of that field so far is sent to the chat; the validated reply replaces it when
the turn is done (a repaired or corrected answer may differ).

Two context variables keep this to the chat's own planning call: SINK is set by the streaming
endpoint for the whole turn, LIVE only around the one model call whose reply the person reads.
Work started during the turn (a task, an agent) never streams into the chat.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextvars import ContextVar

SINK: ContextVar[Callable[[str], None] | None] = ContextVar("reply_sink", default=None)
LIVE: ContextVar[Callable[[str], None] | None] = ContextVar("reply_live", default=None)

FIELD = re.compile(r'"reply"\s*:\s*"')
ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def partial_reply(raw: str) -> str | None:
    """The text of the "reply" field so far, from JSON that may still be arriving; None until
    the field has begun. An escape cut off at the end is left out until the rest arrives."""
    found = FIELD.search(raw or "")
    if not found:
        return None
    out, i, text = [], found.end(), raw
    while i < len(text):
        ch = text[i]
        if ch == '"':
            break
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(text):
            break
        code = text[i + 1]
        if code == "u":
            digits = text[i + 2 : i + 6]
            if len(digits) < 4:
                break
            try:
                out.append(chr(int(digits, 16)))
            except ValueError:
                pass
            i += 6
            continue
        out.append(ESCAPES.get(code, code))
        i += 2
    return "".join(out)


class Relay:
    """Sends the reply text to LIVE whenever it has grown."""

    def __init__(self, emit: Callable[[str], None]):
        self.emit, self.raw, self.sent = emit, "", ""

    def feed(self, chunk: str) -> None:
        self.raw += chunk
        text = partial_reply(self.raw)
        if text and text != self.sent:
            self.sent = text
            self.emit(text)


def sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
