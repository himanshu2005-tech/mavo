"""Mavo's own agents: ready to use, nothing to set up.

Typed in the chat as "@web best budget laptops in India", a message goes straight to that agent
(no planning call): each is a fixed one-time task (task_plan.py) that Mavo runs like any other, so
it shows as a task card in the chat, keeps to the person's default Actions, and reports on the
desktop. Where the job is a known one, Mavo's own code fetches (built-in skills: no model, does
not lose its way) and one writer call answers from what was fetched, citing it. Where the right
source depends on the question (a drive time or places nearby?), a one-time agent picks open data
look-ups from a short list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fastapi import APIRouter

from mavo.schemas import CoordinatorDecision
from mavo.task_plan import TaskPlan

WRITE = (
    "Answer the request in the TASK from the INPUT only, as a short, plain answer: the answer "
    "first, then the key details. Cite what each point comes from as [1], [2] and list those "
    "sources (title and link) at the end. If the input does not answer it, say so plainly; "
    "never fill a gap from memory. Equations in LaTeX ($...$, $$...$$); figures to compare in "
    "a table."
)


@dataclass(frozen=True)
class MavoAgent:
    id: str
    name: str
    tagline: str
    example: str
    fetch: str  # "web", "gather" or "lookups"
    sources: tuple[str, ...] = field(default=())
    calls: int = 4  # model calls the whole task may use

    def plan(self, request: str) -> TaskPlan:
        title = f"{self.name}: {request}"[:80]
        if self.fetch == "lookups":
            steps = [
                {
                    "id": "find",
                    "kind": "agent",
                    "title": f"{self.name} looks it up",
                    "instructions": (
                        f"{request}\n\nUse OPEN DATA lookups (best fit first: "
                        f"{', '.join(self.sources)}). Answer short and plain, with the "
                        "figures exactly as given and the source named."
                    ),
                    "open_data": True,
                    "calls": self.calls,
                }
            ]
            final = "find"
        else:
            fetch = (
                {"skill": "web.read", "params": {"query": request, "pages": 3}}
                if self.fetch == "web"
                else {
                    "skill": "open_data.gather",
                    "params": {"query": request, "sources": list(self.sources)},
                }
            )
            steps = [
                {
                    "id": "fetch",
                    "kind": "skill",
                    "title": f"{self.name} gathers",
                    "instructions": request,
                    **fetch,
                },
                {
                    "id": "answer",
                    "kind": "helper",
                    "role": "writer",
                    "title": "Write the answer",
                    "instructions": WRITE,
                    "uses": ["fetch"],
                },
            ]
            final = "answer"
        return TaskPlan.model_validate(
            {
                "title": title,
                # Said as what to give, not as a heading: "The answer to: X" came back word for
                # word at the top of the answer.
                "deliver": "the answer itself, short and plain, without repeating the question",
                "final": final,
                "model_calls": self.calls + 2,
                "steps": steps,
            }
        )


AGENTS: dict[str, MavoAgent] = {
    a.id: a
    for a in [
        MavoAgent(
            "web",
            "Web",
            "Searches the web, reads the top pages and answers with sources.",
            "@web best budget laptops in India right now",
            "web",
            calls=2,
        ),
        MavoAgent(
            "news",
            "News",
            "The latest headlines on any topic, summed up.",
            "@news Agniveer scheme",
            "gather",
            ("news", "wikipedia"),
            calls=2,
        ),
        MavoAgent(
            "research",
            "Research",
            "Background, papers and books on a topic.",
            "@research lifelong anomaly detection",
            "gather",
            ("wikipedia", "arxiv", "papers", "books"),
            calls=2,
        ),
        MavoAgent(
            "places",
            "Places",
            "Drive times, places nearby, pincodes, sunrise and air quality.",
            "@places drive time from Amrita Chennai to Chennai airport",
            "lookups",
            ("route", "nearby", "geocode", "pincode", "sun", "air", "flight"),
        ),
        MavoAgent(
            "facts",
            "Facts",
            "Exchange rates, word meanings, holidays, food barcodes and alerts.",
            "@facts 250 USD in INR",
            "lookups",
            ("currency", "dictionary", "holidays", "food", "earthquakes", "disasters", "wikipedia"),
        ),
        MavoAgent(
            "dev",
            "Developer",
            "Latest releases, package versions and Stack Overflow answers.",
            "@dev latest fastapi version",
            "lookups",
            ("package", "release", "stackoverflow"),
        ),
    ]
}
ALIASES = {"search": "web", "developer": "dev", "place": "places", "fact": "facts"}
CALL = re.compile(r"^\s*@([A-Za-z]+)\b[\s,:]*(.*)$", re.S)


def decision_for(message: str, open_data_on: bool = True) -> CoordinatorDecision | None:
    """The decision for a message addressed to one of Mavo's agents, or None."""
    found = CALL.match(message or "")
    if not found:
        return None
    name = found[1].lower()
    agent = AGENTS.get(ALIASES.get(name, name))
    if agent is None:
        return None
    request = found[2].strip()
    if not request:
        return CoordinatorDecision(
            reply=f"Ask {agent.name} something, for example: {agent.example}"
        )
    if agent.fetch != "web" and not open_data_on:
        return CoordinatorDecision(
            reply=f"{agent.name} uses Open data, which is off. Turn it on in Resources, then "
            "ask again."
        )
    return CoordinatorDecision(reply=f"{agent.name} is on it.", task=agent.plan(request[:1000]))


def build_agents_api():
    router = APIRouter()

    @router.get("/mavo-agents")
    def listing():
        return [
            {"id": a.id, "name": a.name, "tagline": a.tagline, "example": a.example}
            for a in AGENTS.values()
        ]

    return router
