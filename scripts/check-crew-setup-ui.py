"""Click the real setup UI against intercepted fixtures. Use isolated Vite on port 1432.
No real backend write, Google access, consent, model call or Crew activation is made.
"""

import argparse
import asyncio
from copy import deepcopy
from urllib.parse import urlsplit

from mavo.desktop_actions import installed_browsers
from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:1432")
    parser.add_argument(
        "--mixed",
        action="store_true",
        help="Mail, terminal and script preview controls",
    )
    options = parser.parse_args()
    operations = (
        ["gmail.mark_read", "terminal.run", "project.run"]
        if options.mixed
        else ["calendar.create", "calendar.update", "tasks.create"]
    )
    rule = {
        "enabled": False,
        "mode": "restricted",
        "domains": [],
        "folders": [],
        "apps": [],
        "recipients": [],
        "repositories": [],
    }
    policies = {op: deepcopy(rule) for op in operations}
    caps = (
        ["gmail:read", "gmail:modify"]
        if options.mixed
        else ["calendar:read", "calendar:write", "tasks:read", "tasks:write"]
    )
    draft = {
        "name": "Daily Planner",
        "template_id": "schedule",
        "responsibilities": "Read my calendar and tasks and propose calendar blocks.",
        "trigger_type": "interval",
        "review_interval_minutes": 1440,
        "requested_google_capabilities": caps,
        "requested_google_resources": [],
        "requested_actions": [
            {
                "operation": op,
                "reason": "Requested setup",
                "domains": [],
                "folders": [],
                "apps": [],
                "recipients": [],
            }
            for op in operations
        ],
        "requested_notifications": False,
        "requested_device_metrics": False,
        "daily_call_limit": 30,
        "max_model_calls": 8,
    }
    crew = {
        "id": "fixture-planner",
        "name": "Daily Planner",
        "template_id": "schedule",
        "responsibilities": draft["responsibilities"],
        "configuration_plan": {
            "draft": draft,
            "phase": "needs_access",
            "questions": [],
            "missing": [],
        },
        "status": "draft",
        "is_running": False,
        "runtime_state": "needs_setup",
        "permissions": ["model:invoke", *["google:" + cap for cap in caps]],
        "route_id": "fixture-route",
        "trigger_type": "interval",
        "review_interval_minutes": 1440,
        "daily_call_limit": 30,
        "max_model_calls": 8,
    }
    writes = []

    async def respond(route):
        path = urlsplit(route.request.url).path.removeprefix("/api/v1")
        method = route.request.method
        if method != "GET":
            writes.append(path)
        if path == "/auth/firebase":
            value = {
                "configured": True,
                "signed_in": False,
                "phase": "idle",
                "error": None,
            }
        elif path == "/auth/google":
            value = {
                "configured": True,
                "signed_in": True,
                "phase": "idle",
                "capabilities": caps,
                "error": None,
            }
        elif path == "/action-policies":
            value = {
                "actions": [
                    {"operation": op, "rule": policies[op]} for op in operations
                ]
            }
        elif path == "/crew/fixture-planner/access":
            data = route.request.post_data_json
            policies.update(data["actions"])
            missing = [
                "Allow " + op for op in operations if not policies[op]["enabled"]
            ]
            crew["configuration_plan"]["setup_confirmed"] = True
            crew["configuration_plan"]["missing"] = missing
            if not missing:
                crew["configuration_plan"]["first_run"] = {
                    "status": "passed",
                    "kind": "action_preview",
                    "confirmed": False,
                    "adapter_id": "preview-id",
                    "generation": 7,
                    "message": "Preview ready. Nothing was written.",
                    "measured_calls": 2,
                    "configured_calls": 5,
                    "proposed_actions": [
                        {
                            "operation": "calendar.create",
                            "arguments": {
                                "summary": "Focus block",
                                "start": "2026-11-10T10:00:00+05:30",
                                "end": "2026-11-10T11:00:00+05:30",
                            },
                        }
                    ],
                }
                if options.mixed:
                    crew["configuration_plan"]["first_run"].update(
                        proposed_actions=[
                            {
                                "operation": "gmail.mark_read",
                                "label": "Mark read",
                                "arguments": {"message_id": "observed-mail"},
                                "not_verified": ["execution_result"],
                            },
                            {
                                "operation": "terminal.run",
                                "label": "Run a command",
                                "arguments": {
                                    "command": "system.info",
                                    "folder": "D:\\Sample",
                                },
                                "not_verified": ["execution_result"],
                            },
                            {
                                "operation": "project.run",
                                "label": "Run project",
                                "arguments": {
                                    "project": "Sample",
                                    "tool": "python",
                                    "script": "check.py",
                                },
                                "not_verified": ["script_approval"],
                            },
                        ],
                        unexercised_actions=["gmail.trash"],
                    )
            value = deepcopy(crew)
        elif path == "/crew/fixture-planner/activate-preview":
            assert route.request.post_data_json == {
                "verification_id": "preview-id",
                "generation": 7,
            }
            crew["status"], crew["is_running"] = "active", True
            crew["configuration_plan"]["phase"] = "ready"
            crew["configuration_plan"]["first_run"]["confirmed"] = True
            value = deepcopy(crew)
        else:
            assert method == "GET", "Unexpected test write: " + path
            value = []
        await route.fulfill(json=value)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=next(iter(installed_browsers().values())), headless=True
        )
        try:
            context = await browser.new_context(viewport={"width": 1100, "height": 900})
            await context.add_init_script(
                "window.__TAURI_INTERNALS__={invoke:async()=>({base_url:location.origin,token:'fixture-only-token'}),transformCallback:()=>1,unregisterCallback:()=>{}};window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};localStorage.setItem('mavo-theme','dark');"
            )
            await context.route("**/api/v1/**", respond)
            page = await context.new_page()
            await page.goto(options.base_url)
            await page.wait_for_load_state("networkidle")
            await page.evaluate(
                "async ({crew})=>{const module=await import('/scripts/crew-setup-probe.tsx');module.mountSetup(crew,[{id:'fixture-route',name:'Fixture route',provider:'ollama',model:'fixture',has_credential:true}]);}",
                {"crew": crew},
            )
            panel = page.locator("#setup-probe")
            await panel.get_by_role(
                "combobox", name=operations[0] + " access", exact=True
            ).wait_for()
            await panel.get_by_role(
                "button", name="Test it", exact=True
            ).click()
            await panel.get_by_text("Allow " + operations[0], exact=True).wait_for()
            assert crew["status"] == "draft"
            if options.mixed:
                await panel.get_by_text("More options", exact=True).click()
                await panel.get_by_text("Advanced", exact=True).click()
            for operation in operations:
                await panel.get_by_role(
                    "combobox", name=operation + " access", exact=True
                ).select_option("unrestricted")
            await panel.get_by_role(
                "button", name="Test it", exact=True
            ).click()
            await panel.get_by_text("Nothing was changed yet.", exact=True).wait_for()
            if options.mixed:
                await panel.get_by_text("Mark read", exact=True).wait_for()
                assert "Also allowed to" in await panel.inner_text()
                assert "asks you before running a script" in await panel.inner_text()
            await panel.get_by_role(
                "button", name="Turn it on", exact=True
            ).click()
            await panel.get_by_text("Added to Crew", exact=True).wait_for()
            assert crew["status"] == "active"
            assert writes == [
                "/crew/fixture-planner/access",
                "/crew/fixture-planner/access",
                "/crew/fixture-planner/activate-preview",
            ], writes
            print(
                "PASS: missing actions visible; real controls enabled; preview shown; explicit enable used. Fixture API only, zero model calls."
            )
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
