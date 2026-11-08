"""Opt-in live model/browser check in an isolated workspace, never a personal profile."""

import asyncio
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from mavo.config import Settings
from mavo.credentials import DpapiCredentialStore
from mavo.database import ActionRequest, CrewInstance, ModelRoute
from mavo.main import create_app
from mavo.migrations import run_migrations
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]


async def check(directory, route):
    settings = Settings(
        data_dir=Path(directory),
        require_login=False,
        api_token="isolated-browser-worker-check-token",
        automation_dir=Path(directory) / "browser-tools",
    )
    run_migrations(settings.database_url)
    app = create_app(
        settings,
        credentials=DpapiCredentialStore(
            Path(os.environ["LOCALAPPDATA"]) / "dev.mavo.desktop"
        ),
    )
    runtime = app.state.crew_runtime
    original_chat = runtime.router.chat

    async def checked_chat(*args, **kwargs):
        from mavo.crew_workers import parse_checkpoint
        from pydantic import ValidationError

        raw = await original_chat(*args, **kwargs)
        try:
            parse_checkpoint(raw)
        except ValidationError as exc:
            print(
                json.dumps(
                    {
                        "checkpoint_validation": [
                            {"field": list(error["loc"]), "type": error["type"]}
                            for error in exc.errors()
                        ]
                    }
                )
            )
        return raw

    runtime.router.chat = checked_chat
    operations = ["browser.open", "browser.read", "browser.close"]
    from mavo.action_rules import Rule, RuleChange

    try:
        with runtime.factory() as session:
            session.add(
                ModelRoute(
                    id="route",
                    name="Isolated browser check",
                    provider=route[0],
                    model=route[1],
                    credential_id=route[2],
                )
            )
            session.commit()
            session.add(
                CrewInstance(
                    id="browser-check",
                    name="Isolated browser check",
                    template_id="operator",
                    provider=route[0],
                    model=route[1],
                    route_id="route",
                    responsibilities="Review https://example.com. Use browser.open and browser.read "
                    "to obtain its ACTUAL page title and visible heading. On the next step report "
                    "those exact facts, close the browser, and return wait_for_next_review. "
                    "This is a read-only test with no login, notifications or account changes.",
                    permissions=[
                        "model:invoke",
                        *["action:" + op for op in operations],
                    ],
                    is_running=True,
                    status="active",
                    trigger_type="interval",
                    max_model_calls=3,
                    daily_call_limit=3,
                )
            )
            session.flush()
            from mavo.database import WorkspaceSetting

            for op in operations:
                rule = Rule(enabled=True, domains=["example.com"])
                RuleChange(operation=op, rule=rule, crew_id="browser-check")
                session.add(
                    WorkspaceSetting(
                        key=runtime.actions.rules.key(op, "browser-check"),
                        value={**rule.model_dump(), "revision": "isolated-check"},
                    )
                )
            session.commit()
        for _ in range(3):
            await runtime.work("browser-check")
            with runtime.factory() as session:
                crew = session.get(CrewInstance, "browser-check")
                completed = session.scalars(
                    select(ActionRequest).where(ActionRequest.status == "completed")
                ).all()
                read = next(
                    (a for a in completed if a.operation == "browser.read"), None
                )
                if read and "Example Domain" in (crew.checkpoint or ""):
                    assert read.result["title"] == "Example Domain"
                    assert read.result["text"].strip()
                    print(
                        json.dumps(
                            {
                                "model_calls": crew.daily_calls_used,
                                "completed_actions": [a.operation for a in completed],
                                "page_title_verified": True,
                                "workspace": "isolated",
                            }
                        )
                    )
                    return
                if crew.runtime_state in {"needs_input", "error", "budget_paused"}:
                    raise RuntimeError(
                        crew.last_runtime_error or crew.question or crew.checkpoint
                    )
        raise RuntimeError(
            "The model did not complete the browser review within 3 calls."
        )
    finally:
        await runtime.actions.desktop.close()
        runtime.factory.kw["bind"].dispose()


def main():
    if "--live-model" not in __import__("sys").argv:
        raise SystemExit(
            "Pass --live-model to spend at most 3 calls on the saved AIRouter agent route."
        )
    database = Path(os.environ["LOCALAPPDATA"]) / "dev.mavo.desktop/mavo.db"
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        route = connection.execute(
            "SELECT provider,model,credential_id FROM model_routes "
            "WHERE provider='airouter' ORDER BY created_at LIMIT 1"
        ).fetchone()
    if not route:
        raise RuntimeError("Save an AIRouter agent connection first.")
    with tempfile.TemporaryDirectory(
        prefix="browser-worker-", dir=ROOT / ".build"
    ) as directory:
        asyncio.run(check(directory, route))


if __name__ == "__main__":
    main()
