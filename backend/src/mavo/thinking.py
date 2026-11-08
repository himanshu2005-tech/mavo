"""Sequential thinking, shown to the person.

Every model step of a task thinks in a few numbered steps before it answers, inside
<thinking>...</thinking>. Mavo takes that block out of the answer (so files, verdicts and results
are exactly as before) and keeps it on the step, where the task card shows it as a short
timeline: what it understood, what it was given, how it went about it, what it checked.

It is the model's own account, written for the person: plain, short, never secrets.
"""

from __future__ import annotations

import re

# Worded as written working steps, not "think": a reasoning model (DeepSeek) asked to "think
# inside <thinking>" did it in its hidden reasoning instead, and a reviewer's reply came back as
# only "VERDICT: fix" with the review itself gone.
THINK = (
    "\n\nSTART your reply with your working steps, written out, inside <steps></steps>: 3 to 6 "
    "numbered lines, each ONE short line as `N. Label: what you did or decided` (labels such as "
    "Goal, Given, Approach, Risks, Check). They are shown to the person as how you went about "
    "it, so write them plainly and never put passwords, keys or codes in them. Then, after "
    "</steps>, write your COMPLETE answer exactly as asked above (every file, edit, finding and "
    "line it needs): the steps are extra and never replace any part of it."
)

BLOCK = re.compile(r"<(steps|thinking)>(.*?)</\1>\s*", re.S | re.I)
STEP = re.compile(
    r"^\s*(?:\d+[.)]|[-*•])\s*(?:\*\*)?([A-Za-z][\w &/'-]{0,28}?)(?:\*\*)?\s*:\s*(.+)$"
)
MAX_STEPS, MAX_CHARS = 6, 200


def split_thinking(text: str) -> tuple[str, list[dict]]:
    """The answer without its thinking, and the thinking as [{label, text}] steps."""
    # The block with steps in it: a real model first repeated the instruction (which names an
    # empty "<steps></steps>"), and that empty pair was taken for its thinking.
    blocks = list(BLOCK.finditer(text or ""))
    found = (
        next(
            (b for b in blocks if any(STEP.match(x.strip()) for x in b.group(2).splitlines())), None
        )
        or next((b for b in blocks if b.group(2).strip()), None)
        or (blocks[0] if blocks else None)
    )
    if found and re.search(r"working steps", text[: found.start()], re.I):
        text = text[found.start() :]  # the repeated instruction before it is not the answer
        found = BLOCK.search(text)
    if not found:
        # A reply that opened its thinking and never closed it: the rest is still the answer.
        opened = re.match(r"\s*<(steps|thinking)>\s*", text or "", re.I)
        return ((text or "")[opened.end() :] if opened else (text or "")).strip(), []
    answer = (text[: found.start()] + text[found.end() :]).strip()
    # Only labelled steps count, each one short line: a real reviewer filled its steps with a
    # running monologue ("Is there an issue with ... No. Need perhaps inspect ..."), and that is
    # not a structured account.
    steps = [
        {"label": step[1].strip(), "text": short(step[2])}
        for step in (STEP.match(line.strip()) for line in found.group(2).splitlines())
        if step
    ]
    return answer, steps[:MAX_STEPS]


def short(text: str) -> str:
    """One step, at most MAX_CHARS, cut at a sentence or word, never mid-word."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_CHARS:
        return text
    cut = text[:MAX_CHARS]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return cut[: end + 1] if end > MAX_CHARS // 2 else cut[: cut.rfind(" ")] + "…"
