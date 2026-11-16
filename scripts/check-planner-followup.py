"""Bounded planner-only reproduction. No Crew activation or provider tools.

Default validates a saved draft without inference. --live makes at most --max-calls requests using
the configured publisher model and secret in memory. Output is diagnostics only;
no prompts, model output, personal data, or credentials are printed or saved.
"""

import argparse
import asyncio
import json
import os
import sqlite3
from pathlib import Path

import httpx
from mavo.coordinator_contract import (
    GatewayTurn,
    PlannerContext,
    coordinator_prompt,
    parse_decision,
)
from mavo.credentials import DpapiCredentialStore
from mavo.publisher_gateway import GatewaySettings, validation_issues
from pydantic import ValidationError


def context_from_saved_draft():
    path = Path(os.environ["LOCALAPPDATA"]) / "dev.mavo.desktop/mavo.db"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT id,configuration_plan FROM crew_instances WHERE name='Daily Planner' AND status='draft' AND hidden=0 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise RuntimeError("No Daily Planner draft found; nothing was changed.")
    return PlannerContext(
        local_time="2026-11-09T21:40:00+05:30",
        current_crew_id=row[0],
        current_plan=json.loads(row[1])["draft"],
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-calls", type=int, choices=[1, 2, 3], default=1)
    parser.add_argument(
        "--reference", help="Copy the saved failed turn in memory; never replay work."
    )
    args = parser.parse_args()
    context = context_from_saved_draft()
    messages = [
        {"role": "system", "content": coordinator_prompt(context)},
        {
            "role": "user",
            "content": "Create an agent which plans my everyday tasks, checks my mails and updates my schedule accordingly.",
        },
        {
            "role": "assistant",
            "content": "Should it also draft replies to the mails it flags, or just summarize them?",
        },
        {
            "role": "user",
            "content": "Just summarise them for now. 1. 7-8: gym everyday. 2. breakfast after I come home from gym. 3. lunch at 1. 4. dinner at 8:30.",
        },
    ]
    if args.reference:
        path = Path(os.environ["LOCALAPPDATA"]) / "dev.mavo.desktop/mavo.db"
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT request_payload FROM coordinator_turns WHERE id=?",
                (args.reference,),
            ).fetchone()
        if not row:
            raise RuntimeError("Saved turn not found.")
        saved = GatewayTurn.model_validate(json.loads(row[0])["turn"])
        context = saved.context
        messages = [
            {"role": "system", "content": coordinator_prompt(context)},
            *[m.model_dump() for m in saved.messages],
        ]
    if not args.live:
        print(
            {
                "mode": "validation_only",
                "draft_valid": context.current_plan is not None,
                "workspace_written": False,
            }
        )
        return
    settings = GatewaySettings()
    secret = (
        settings.api_key.get_secret_value()
        if settings.api_key
        else DpapiCredentialStore(settings.data_dir).get("publisher-airouter")
    )
    if not secret:
        raise RuntimeError("No publisher credential configured.")
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(args.max_calls):
            print(f"Planning request {attempt + 1}/{args.max_calls}", flush=True)
            response = await client.post(
                "https://api.airouter.in/v1/chat/completions",
                headers={"Authorization": "Bearer " + secret},
                json={
                    "model": settings.model,
                    "messages": messages,
                    "max_tokens": settings.max_output_tokens,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "reasoning_effort": settings.reasoning_effort,
                },
            )
            if response.status_code != 200:
                print({"status": "provider_error", "http_status": response.status_code})
                return
            choice = response.json()["choices"][0]
            raw = choice["message"]["content"]
            if choice.get("finish_reason") == "length":
                print({"status": "truncated"})
                return
            try:
                decision = parse_decision(raw)
            except ValidationError as error:
                # Known operation validators contain fixed developer-owned text only.
                issues = validation_issues(error)
                print({"status": "invalid", "issues": issues}, flush=True)
                if attempt + 1 == args.max_calls:
                    return
                from mavo.publisher_gateway import describe

                messages += [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": "Correct the JSON schema only: " + describe(error),
                    },
                ]
            except ValueError:
                print({"status": "invalid_json"})
                return
            else:
                print(
                    {
                        "status": "passed",
                        "questions": len(decision.questions),
                        "proposal": bool(decision.crew_proposal),
                        "configuration_change": bool(decision.configuration_change),
                        "task": bool(decision.task),
                        "calls": attempt + 1,
                        "workspace_written": False,
                        "model": settings.model,
                    },
                    flush=True,
                )
                return


if __name__ == "__main__":
    asyncio.run(main())
