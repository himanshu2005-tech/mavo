"""Isolated News setup reproduction. Never activates or edits the real user's Crew.

--simulate: synthetic feeds and model, zero network/credentials.
--live-airouter: saved news draft/route, public feeds, at most TWO paid HTTP requests.
Reports contain field names and match counts, never keys, prompts or raw model answers.
"""

import argparse
import json
import os
import secrets
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient
from mavo.config import Settings
from mavo.credentials import DpapiCredentialStore, MemoryCredentialStore
from mavo.database import CrewInstance
from mavo.main import create_app
from mavo.model_router import ModelProviderError
from mavo.schemas import CoordinatorDecision, CrewDraft
from mavo.source_snapshots import source_strings

ROOT = Path(__file__).resolve().parents[1]


class NewsTransport(httpx.AsyncBaseTransport):
    def __init__(self, simulate, limit=2):
        self.simulate = simulate
        self.limit = limit
        self.calls = 0
        self.tokens = 0
        self.diagnostics = []

    async def handle_async_request(self, request):
        if (request.method, request.url.host, request.url.path) != (
            "POST",
            "api.airouter.in",
            "/v1/chat/completions",
        ) or self.calls >= self.limit:
            raise ModelProviderError(
                "Isolated news test request limit reached; no further paid call made"
            )
        self.calls += 1
        print(
            f"Inference {self.calls}/{self.limit} ({'simulated' if self.simulate else 'live'})",
            flush=True,
        )
        payload = json.loads(request.content)
        prompt = payload["messages"][1]["content"]
        marker = "Authorized UNTRUSTED connected-account snapshots: "
        snapshots, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
        items = [item for snap in snapshots.values() for item in snap.get("items", [])]
        if self.simulate:
            item = items[0]
            content = json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": item["title"],
                    "source_items": [{"source_ref": item["source_ref"]}],
                }
            )
            response = httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": content},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        else:
            async with httpx.AsyncHTTPTransport(retries=0) as transport:
                response = await transport.handle_async_request(request)
                await response.aread()
        data = response.json()
        self.tokens += int((data.get("usage") or {}).get("total_tokens") or 0)
        if response.status_code == 200:
            from mavo.crew_workers import parse_checkpoint

            content = data["choices"][0]["message"].get("content") or ""
            try:
                checkpoint = parse_checkpoint(content)
                evidence = checkpoint.evidence
                self.diagnostics.append(
                    {
                        "request": self.calls,
                        "state": checkpoint.state,
                        "input_items": len(items),
                        "evidence_count": len(evidence),
                        "selected_items": len(checkpoint.source_items),
                        "unknown_references": sum(
                            s.source_ref
                            not in {item.get("source_ref") for item in items}
                            for s in checkpoint.source_items
                        ),
                        "evidence_matches": [
                            {
                                "url": any(e.url == i.get("url") for i in items),
                                "quote": any(
                                    e.quote in "\n".join(source_strings(i))
                                    for i in items
                                ),
                                "same_item": any(
                                    e.url == i.get("url")
                                    and e.quote in "\n".join(source_strings(i))
                                    for i in items
                                ),
                                "quote_characters": len(e.quote),
                            }
                            for e in evidence
                        ],
                        "has_question": bool(checkpoint.question),
                        "summary_characters": len(checkpoint.summary),
                        "finish_reason": data["choices"][0].get("finish_reason"),
                    }
                )
            except ValueError:
                self.diagnostics.append(
                    {"request": self.calls, "checkpoint": "invalid"}
                )
        return response


def saved_news():
    directory = Path(os.environ["LOCALAPPDATA"]) / "dev.mavo.desktop"
    with sqlite3.connect((directory / "mavo.db").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT c.configuration_plan,c.permissions,r.model,r.credential_id "
            "FROM crew_instances c JOIN model_routes r ON r.id=c.route_id "
            "WHERE c.hidden=0 AND c.status IN ('draft','active','paused') AND r.provider='airouter' "
            "AND c.name LIKE '%News%' ORDER BY c.updated_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise RuntimeError("No AIRouter news draft found; no account was changed")
    draft = CrewDraft.model_validate(json.loads(row["configuration_plan"])["draft"])
    permissions = json.loads(row["permissions"])
    if any(
        not p.startswith("feeds:") and p not in {"model:invoke", "notifications:show"}
        for p in permissions
    ):
        raise RuntimeError("This reproduction is restricted to public feeds only")
    key = DpapiCredentialStore(directory).get(row["credential_id"])
    if not key:
        raise RuntimeError("Saved route credential is unavailable")
    return draft, permissions, row["model"], key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--live-airouter", action="store_true")
    parser.add_argument("--max-calls", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    if args.simulate:
        capabilities = ["feeds:bbc-tech:read"]
        draft = CrewDraft(
            name="News Watch",
            template_id="research",
            responsibilities="Report news every hour",
            trigger_type="interval",
            max_model_calls=12,
            daily_call_limit=24,
            requested_notifications=True,
            requested_connector_capabilities=capabilities,
        )
        permissions = ["model:invoke", "notifications:show", *capabilities]
        model, key = "fixture", "fixture-key"
    else:
        draft, permissions, model, key = saved_news()
    transport = NewsTransport(args.simulate, args.max_calls)
    with TemporaryDirectory(prefix="news-setup-", dir=ROOT / ".build") as directory:
        settings = Settings(
            _env_file=None,
            recipes=False,  # the agent-per-run path this check covers
            first_run_in_background=False,  # it reads the setup test's result at once
            data_dir=Path(directory),
            workers_enabled=False,
            require_login=False,
            api_token=secrets.token_urlsafe(32),
        )
        app = create_app(
            settings, credentials=MemoryCredentialStore(), cloud_transport=transport
        )
        runtime = app.state.crew_runtime
        # The model transport rejects non-inference requests. Public feeds use the
        # existing curated, bounded adapter with its own normal HTTP transport.
        if not args.simulate:
            runtime.connectors.feeds.transport = None
        with TestClient(app) as client:
            client.headers["Authorization"] = "Bearer " + settings.api_token
            route = client.post(
                "/api/v1/routes",
                json={
                    "name": "Isolated news",
                    "provider": "airouter",
                    "model": model,
                    "api_key": key,
                },
            )
            route.raise_for_status()
            route_id = route.json()["id"]
            del key
            draft.route_id = route_id
            if draft.composition:
                for agent in draft.composition.agents:
                    agent.route_id = None
            feeds = [p for p in permissions if p.startswith("feeds:")]
            runtime.connectors.feeds.configure([p.split(":")[1] for p in feeds])
            if args.simulate:
                runtime.connectors.read = AsyncMock(
                    return_value={
                        "provider": "feeds",
                        "items": [
                            {
                                "id": "fixture-story",
                                "title": "City library opens evening study spaces for students",
                                "url": "https://example.org/library",
                            }
                        ],
                    }
                )
            with runtime.factory() as session:
                crew = runtime.planner.apply_plan(
                    session,
                    CoordinatorDecision(
                        reply="Isolated verification",
                        crew_proposal=draft,
                    ),
                    None,
                )
                crew_id = crew.id
                session.commit()
            result = client.post(
                f"/api/v1/crew/{crew_id}/access",
                json={
                    "route_id": route_id,
                    "connector_capabilities": feeds,
                    "notifications": True,
                },
            )
            result.raise_for_status()
            with runtime.factory() as session:
                record = session.get(CrewInstance, crew_id).configuration_plan.get(
                    "first_run", {}
                )
            report = {
                "mode": "simulated"
                if args.simulate
                else "live AIRouter + public feeds",
                "model": model,
                "requests": transport.calls,
                "tokens": transport.tokens,
                "user_workspace_changed": False,
                "first_run": record,
                "diagnostics": transport.diagnostics,
            }
            # Deliberately omit the preview: no raw answers or prompts in diagnostics.
            report["first_run"] = {k: v for k, v in record.items() if k != "preview"}
            print(json.dumps(report, indent=2), flush=True)
            return 0 if record.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
