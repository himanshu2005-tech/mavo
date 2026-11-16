"""Real activity UI with deliberately mixed fixture events; no live account or model access."""

import asyncio
from urllib.parse import urlsplit

from mavo.desktop_actions import installed_browsers
from playwright.async_api import async_playwright


async def main():
    queries = []
    rows = []
    for identifier, chat, status, kind in [
        ("news", None, "recorded", "completion"),
        ("planner", "day-chat", "pending", "question"),
        ("other", "other-chat", "pending", "question"),
        ("permission", None, "pending", "question"),
    ]:
        rows.append(
            {
                "id": identifier,
                "seq": len(rows) + 1,
                "conversation_id": chat,
                "sender": "core",
                "crew_id": None,
                "task_id": None,
                "step_id": None,
                "status": status,
                "kind": kind,
                "revision": 0,
                "created_at": "2026-11-09T00:00:00Z",
                "resolved_at": None,
                "payload": {"summary": identifier + " fixture update"},
            }
        )

    async def reply(route):
        url = urlsplit(route.request.url)
        assert route.request.method == "GET"
        if url.path.endswith("/auth/firebase"):
            value = {
                "configured": True,
                "signed_in": False,
                "phase": "idle",
                "email": None,
                "name": None,
                "error": None,
            }
        elif url.path.endswith("/orchestration/events"):
            queries.append(url.query)
            value = rows  # malicious/stale/old backend returning more than requested
        else:
            raise AssertionError("Unexpected request " + url.path)
        await route.fulfill(json=value)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=next(iter(installed_browsers().values())), headless=True
        )
        try:
            context = await browser.new_context()
            await context.add_init_script(
                "window.__TAURI_INTERNALS__={invoke:async()=>({base_url:location.origin,token:'fixture-only'}),transformCallback:()=>1,unregisterCallback:()=>{}};"
            )
            await context.route("**/api/v1/**", reply)
            page = await context.new_page()
            await page.goto("http://localhost:1420/")
            await page.get_by_role("button", name="Sign in", exact=True).wait_for()

            async def mount(chat=None, workspace=False):
                await page.evaluate(
                    "async({chat,workspace})=>{const m=await import('/scripts/activity-scope-probe.tsx');m.mountActivity(chat??undefined,workspace)}",
                    {"chat": chat, "workspace": workspace},
                )

            await mount()
            await page.wait_for_timeout(200)
            assert queries == []  # New chat must never query the global workspace feed.
            await mount("day-chat")
            panel = page.locator("#activity-scope-probe")
            await panel.get_by_text("planner fixture update", exact=True).wait_for()
            assert (
                await panel.get_by_text("news fixture update", exact=True).count() == 0
            )
            assert (
                await panel.get_by_text("other fixture update", exact=True).count() == 0
            )
            assert queries[-1] == "conversation_id=day-chat"
            await mount("other-chat")
            await panel.get_by_text("other fixture update", exact=True).wait_for()
            assert (
                await panel.get_by_text("planner fixture update", exact=True).count()
                == 0
            )
            await mount(workspace=True)
            await panel.get_by_text("permission fixture update", exact=True).wait_for()
            assert (
                await panel.get_by_text("news fixture update", exact=True).count() == 0
            )
            await mount()
            await page.wait_for_timeout(200)
            assert await panel.locator(".conversation-activity").count() == 0
            print(
                "PASS: empty chat makes no global request; scoped chats reject mixed/other-Crew events; workspace retains pending requests. Fixtures only, zero model calls."
            )
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
