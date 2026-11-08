# ruff: noqa: E501
"""The real thing: a Crew with the user's own agent connection works a task end to end through
the real worker (planning, actions, policy, browser). Slow and billed (a handful of model calls
per task), so it is a script, not a test.

    python -m tests.browser_agent.model_runs --data <copy of the app data dir> --profile <automation dir>

It reads a COPY of the data and browser profile; nothing in the live app is touched."""

import asyncio
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from mavo.config import Settings
from mavo.database import ActionRequest, CrewInstance, CrewRun
from mavo.main import create_app

TRACE = "--trace" in sys.argv
TOKEN = "x" * 32
H = {"Authorization": "Bearer " + TOKEN}
BROWSER = [
    "browser.open",
    "browser.read",
    "browser.click",
    "browser.fill",
    "browser.select",
    "browser.press",
    "browser.scroll",
    "browser.wait",
    "browser.navigate",
]


def verify_httpbin(texts, answer):
    blob = " ".join(texts)
    wanted = [
        '"custname": "Asha Rao"',
        '"custtel": "9876543210"',
        '"custemail": "asha@example.com"',
        '"size": "medium"',
        "bacon",
        "cheese",
        "18:30",
        "Ring twice",
    ]
    missing = [w for w in wanted if w not in blob]
    return (
        not missing,
        "server echoed every field" if not missing else f"server did not echo {missing}",
    )


def verify_wikipedia(texts, answer):
    return bool(
        re.search(r"\d[\d,]{4,}", answer)
    ) and "chennai" in answer.lower(), f"answer: {answer[:140]!r}"


def verify_quotes(texts, answer):
    return "marilyn monroe" in answer.lower(), f"answer: {answer[:140]!r}"


def verify_portal(texts, answer):
    return "CEI INDIA" in answer and (
        "3 Nov" in answer or "3 November" in answer
    ), f"answer: {answer[:200]!r}"


TASKS = [
    {
        "name": "Form filling (httpbin pizza order)",
        "domains": ["httpbin.org"],
        "calls": 8,
        "text": "Open https://httpbin.org/forms/post and place a pizza order with these exact details: customer name Asha Rao, telephone 9876543210, email asha@example.com, size medium, toppings bacon and extra cheese, preferred delivery time 18:30, delivery instructions 'Ring twice'. Submit it, then report what the server received.",
        "verify": verify_httpbin,
    },
    {
        "name": "Search and extract (Wikipedia: Chennai population)",
        "domains": ["en.wikipedia.org"],
        "calls": 8,
        "text": "On https://en.wikipedia.org find the article about Chennai and tell me the city's population, quoting the number exactly as written there.",
        "verify": verify_wikipedia,
    },
    {
        "name": "Pagination (quotes site, page 2)",
        "domains": ["quotes.toscrape.com"],
        "calls": 8,
        "text": "Open https://quotes.toscrape.com, go to the second page of quotes using the Next link, and tell me who said the first quote on that page.",
        "verify": verify_quotes,
    },
    {
        "name": "Portal scan (placements jobs, all pages)",
        "domains": ["placements.haveloc.com"],
        "calls": 12,
        "text": "Open https://placements.haveloc.com/jobs. The table is paginated. Go through every page (use the page-size control or Next) and list every job whose Status is exactly 'Open For Applications': company, role, and the Apply Before date, quoted exactly.",
        "verify": verify_portal,
    },
]


LOOP = asyncio.new_event_loop()  # one loop for the whole run: the browser belongs to it


async def drive(runtime, crew_id, limit, trace=False):
    for step in range(limit):
        await runtime.work(crew_id)
        with runtime.factory() as session:
            if trace:
                last = session.scalars(
                    select(CrewRun)
                    .where(CrewRun.crew_id == crew_id)
                    .order_by(CrewRun.created_at.desc())
                ).first()
                done = session.scalars(
                    select(ActionRequest)
                    .where(ActionRequest.crew_id == crew_id)
                    .order_by(ActionRequest.created_at.desc())
                    .limit(6)
                ).all()[::-1]
                print(
                    f"  step {step + 1}: {last.status}: {(last.result or last.error or '')[:260]!r}",
                    flush=True,
                )
                for a in done:
                    print(
                        f"     {a.operation} {json.dumps(a.arguments)[:110]} -> {a.status} {(a.error or '')[:120]}",
                        flush=True,
                    )
            crew = session.get(CrewInstance, crew_id)
            soon = (
                crew.next_run_at
                and (
                    crew.next_run_at.replace(tzinfo=crew.next_run_at.tzinfo or UTC)
                    - datetime.now(UTC)
                ).total_seconds()
                < 15
            )
            if crew.runtime_state in {"needs_input", "error", "waiting_event"} or not soon:
                break


def run_task(client, runtime, route, task):
    crew = client.post(
        "/api/v1/crew",
        headers=H,
        json={
            "template_id": "operator",
            "name": task["name"][:70],
            "route_id": route["id"],
            "trigger_type": "manual",
            "responsibilities": task["text"],
            "max_model_calls": task["calls"],
            "daily_call_limit": 100,
        },
    ).json()
    for op in BROWSER:
        rule = {"enabled": True, "mode": "restricted", "domains": task["domains"]}
        client.put(
            "/api/v1/action-policies",
            headers=H,
            json={"operation": op, "crew_id": crew["id"], "rule": rule},
        )
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=H)
    started = time.monotonic()
    LOOP.run_until_complete(drive(runtime, crew["id"], task["calls"], TRACE))
    seconds = round(time.monotonic() - started, 1)
    LOOP.run_until_complete(
        runtime.actions.desktop.close()
    )  # the next task starts with a free browser
    with runtime.factory() as session:
        runs = session.scalars(
            select(CrewRun).where(CrewRun.crew_id == crew["id"]).order_by(CrewRun.created_at)
        ).all()
        actions = session.scalars(
            select(ActionRequest)
            .where(ActionRequest.crew_id == crew["id"])
            .order_by(ActionRequest.created_at)
        ).all()
        stored = session.get(CrewInstance, crew["id"])
        answer = (stored.checkpoint or "") + " " + " ".join(r.result or "" for r in runs[-1:])
        texts = [
            (a.result or {}).get("text", "") + " " + (a.result or {}).get("view", "")
            for a in actions
            if isinstance(a.result, dict)
        ]
        ok, detail = task["verify"](texts, answer)
        return {
            "name": task["name"],
            "passed": ok,
            "detail": detail,
            "seconds": seconds,
            "model_calls": sum(r.model_calls for r in runs),
            "actions": [f"{a.operation}:{a.status}" for a in actions],
            "state": stored.runtime_state,
            "question": (stored.observation or {}).get("question"),
            "errors": [a.error for a in actions if a.error][:3],
        }


def main(data, profile, only=None):
    settings = Settings(
        data_dir=Path(data),
        automation_dir=Path(profile),
        api_token=TOKEN,
        require_login=False,
        workers_enabled=False,
    )
    results = []
    with TestClient(create_app(settings)) as client:
        runtime = client.app.state.crew_runtime
        route = client.get("/api/v1/routes", headers=H).json()[0]
        for task in TASKS:
            if only and not any(part in task["name"].lower() for part in only.split(",")):
                continue
            try:
                results.append(run_task(client, runtime, route, task))
            except Exception as exc:
                results.append(
                    {
                        "name": task["name"],
                        "passed": False,
                        "detail": f"{type(exc).__name__}: {exc}"[:200],
                        "seconds": 0,
                        "model_calls": 0,
                        "actions": [],
                    }
                )
            r = results[-1]
            print(
                ("PASS" if r["passed"] else "FAIL"),
                r["name"],
                f"{r['seconds']}s",
                f"{r['model_calls']} model calls",
                f"{len(r['actions'])} actions",
                "|",
                r["detail"],
                flush=True,
            )
    return results


if __name__ == "__main__":
    args = sys.argv
    data, profile = args[args.index("--data") + 1], args[args.index("--profile") + 1]
    only = args[args.index("--only") + 1].lower() if "--only" in args else None
    out = main(data, profile, only)
    print(json.dumps(out, indent=1, default=str))
