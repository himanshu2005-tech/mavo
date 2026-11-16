"""Exercise the real frontend entry before login, after login, and on module-load failure.

Use a local Vite server on 1420. All API replies are isolated fixtures; no account changes.
"""

import asyncio
import json
import os

from mavo.desktop_actions import installed_browsers
from playwright.async_api import async_playwright

BASE = os.environ.get("MAVO_UI_TEST_URL", "http://localhost:1420")


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=next(iter(installed_browsers().values())), headless=True
        )
        for signed_in, startup_failures in (
            (False, 0),
            (True, 0),
            (False, 6),
            (True, 6),
        ):
            page = await browser.new_page()
            errors = []
            remaining = startup_failures
            core_reads = []
            core_phase = {"ready": False}
            page.on("pageerror", lambda e, captured=errors: captured.append(str(e)))
            await page.add_init_script("""window.__TAURI_INTERNALS__ = {
                invoke: async command => {
                    if (command === 'backend_config') return {base_url: location.origin, token: 'fixture'};
                    if (command === 'plugin:event|listen') return 1;
                    return null;
                }, transformCallback: () => 1, unregisterCallback: () => {}
            }; window.__TAURI_EVENT_PLUGIN_INTERNALS__ = {unregisterListener: () => {}};
            window.startupAlerts=[];
            new MutationObserver(()=>{
                for(const node of document.querySelectorAll('[role=alert]'))
                    if(node.textContent.trim())window.startupAlerts.push(node.textContent);
            }).observe(document,{subtree:true,childList:true,characterData:true});""")

            async def api(
                route,
                _request,
                expected_signed_in=signed_in,
                reads=core_reads,
                core=core_phase,
            ):
                nonlocal remaining
                assert route.request.method == "GET", "Startup must not mutate accounts"
                path = route.request.url.split("/api/v1/")[-1]
                if path == "auth/firebase":
                    if remaining:
                        remaining -= 1
                        await route.abort("connectionrefused")
                        return
                    data = {
                        "configured": True,
                        "signed_in": expected_signed_in,
                        "pending": None,
                        "email": None,
                        "error": None,
                    }
                elif path.startswith("core/status"):
                    reads.append(path)
                    connected = core["ready"]
                    data = {
                        "configured": True,
                        "connected": connected,
                        "provider": "Mavo Core",
                        "model": None,
                        "error": None if connected else "Core startup fixture",
                    }
                elif path == "status":
                    data = {
                        "service": "mavo",
                        "version": "test",
                        "database": "connected",
                        "providers": [],
                    }
                elif path == "onboarding":
                    data = {
                        "version": 1,
                        "step": "complete",
                        "revision": 3,
                        "account_key": "startup-fixture",
                        "completed_at": "2026-11-08T00:00:00Z",
                        "providers_skipped": False,
                    }
                elif path == "auth/google":
                    data = {"connected": False}
                elif path.startswith("runtime/activity"):
                    data = {
                        "stream_id": "fixture",
                        "cursor": 0,
                        "reset": False,
                        "events": [],
                        "active_runs": [],
                        "active_calls": [],
                    }
                else:
                    data = []
                await route.fulfill(json=data)

            await page.route("**/api/v1/**", api)
            await page.goto(BASE + "/")
            if startup_failures:
                await page.get_by_role(
                    "heading", name="Starting Mavo", exact=True
                ).wait_for()
                assert (
                    await page.get_by_role("button", name="Sign in", exact=True).count()
                    == 0
                )
            if signed_in:
                await page.locator(".graph-canvas").wait_for(timeout=20_000)
                await page.get_by_text("Core startup fixture", exact=True).wait_for()
                core_phase["ready"] = True
                if not startup_failures:
                    await page.get_by_role(
                        "button", name="Check connection", exact=True
                    ).click()
                await page.get_by_text("Core startup fixture", exact=True).wait_for(
                    state="hidden", timeout=15_000
                )
                assert len(core_reads) >= 2
                if not startup_failures:
                    assert "core/status?refresh=true" in core_reads
            else:
                await page.get_by_role("button", name="Sign in", exact=True).wait_for(
                    timeout=20_000
                )
                assert await page.get_by_role(
                    "heading", name="Welcome to Mavo"
                ).is_visible()
            assert not errors, errors
            assert await page.evaluate("window.startupAlerts") == []
            assert await page.locator("#mavo-startup").count() == 0
            await page.close()
        # A genuine unavailable sidecar must not be hidden behind an infinite spinner
        # or presented as a Google logout. Retry returns to a verified sign-in screen.
        page = await browser.new_page()
        await page.add_init_script("""window.__TAURI_INTERNALS__={
            invoke:async()=>({base_url:location.origin,token:'fixture'}),
            transformCallback:()=>1,unregisterCallback:()=>{}};""")
        unavailable = True

        async def outage(route):
            if unavailable:
                await route.abort("connectionrefused")
            else:
                await route.fulfill(
                    json={
                        "configured": True,
                        "signed_in": False,
                        "email": None,
                        "name": None,
                        "phase": "idle",
                        "error": None,
                    }
                )

        await page.route("**/api/v1/**", outage)
        await page.clock.install()
        await page.goto(BASE + "/")
        await page.get_by_role("heading", name="Starting Mavo", exact=True).wait_for()
        await page.clock.run_for(100)
        await page.clock.fast_forward(9000)
        await page.get_by_role("button", name="Retry connection", exact=True).wait_for()
        assert await page.get_by_role("alert").count() == 1
        assert "local service" in await page.get_by_role("alert").inner_text()
        assert await page.get_by_role("button", name="Sign in", exact=True).count() == 0
        unavailable = False
        await page.get_by_role("button", name="Retry connection", exact=True).click()
        await page.get_by_role("button", name="Sign in", exact=True).wait_for()
        assert await page.get_by_role("alert").count() == 0
        await page.close()
        # A module that cannot load must offer recovery, never a blank white root.
        page = await browser.new_page()
        # Vite appends a timestamp after HMR; abort that module too, not just a bare URL.
        await page.route("**/src/main.tsx*", lambda route: route.abort())
        await page.goto(BASE + "/")
        await page.get_by_role("button", name="Reload Mavo", exact=True).wait_for()
        assert await page.get_by_role("alert").is_visible()
        assert (
            await page.locator("body").evaluate(
                "el => getComputedStyle(el).backgroundColor"
            )
            == "rgb(16, 16, 16)"
        )
        print(
            json.dumps(
                {
                    "signed_out_login": "passed",
                    "signed_in_workspace": "passed",
                    "brief_backend_warmup": "passed",
                    "core_health_auto_recovery": "passed",
                    "manual_health_refresh": "passed",
                    "prolonged_outage_retry": "passed",
                    "broken_import_recovery": "passed",
                    "real_account_changes": 0,
                }
            )
        )
        await browser.close()


asyncio.run(main())
