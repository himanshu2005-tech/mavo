"""Exercise the real status-window UI with synthetic alerts and mocked native IPC.

Requires Vite on localhost:1420. No real workspace, credentials, model calls,
notifications, or operating-system windows are touched.
"""

import argparse
import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from mavo.desktop_actions import installed_browsers
from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:1420")
    args = parser.parse_args()
    due = []
    now = datetime.now(UTC).isoformat()

    def alert(identifier):
        return {
            "id": identifier,
            "crew_id": "fixture-news",
            "crew_name": identifier,
            "kind": "crew_update",
            "message": "Verified synthetic news digest\n\n\\section*{News Reader}\n\n"
            + "\\<br>".join(
                f"- Story {i}: A source-checked news item with enough detail to read "
                "in a scrollable notification rather than hiding additional information. "
                f"[Source](https://example.org/story/{i})"
                for i in range(1, 9)
            ),
            "scheduled_for": now,
            "local_date": now[:10],
        }

    async def respond(route):
        path = urlsplit(route.request.url).path.removeprefix("/api/v1")
        if path == "/auth/firebase":
            value = {"signed_in": True}
        elif path == "/desktop-status":
            value = {
                "paused": False,
                "running": 1,
                "pending": len(due),
                "needs": [],
                "working": [],
            }
        elif path == "/reminders/due":
            value = list(due)
        elif path.endswith("/dismiss"):
            assert route.request.method == "POST"
            identifier = unquote(path.split("/")[-2])
            due[:] = [item for item in due if item["id"] != identifier]
            await route.fulfill(status=204)
            return
        else:
            raise AssertionError("Unexpected API call " + path)
        await route.fulfill(json=value)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=next(iter(installed_browsers().values())), headless=True
        )
        try:
            context = await browser.new_context(viewport={"width": 440, "height": 420})
            await context.add_init_script("""
                let sequence=0, pending=null;
                window.isTauri=true;
                const callbacks=new Map(), listeners=new Map();
                window.fixtureShows=[];
                window.fixtureResizes=[];
                window.fixtureLinks=[];
                const emit=(event,payload)=>{
                  for(const [id,l] of listeners)if(l.event===event)callbacks.get(l.handler)?.({event,id,payload});
                };
                window.__TAURI_INTERNALS__={
                  metadata:{currentWindow:{label:'status'},currentWebview:{label:'status'}},
                  transformCallback:fn=>{const id=++sequence;callbacks.set(id,fn);return id;},
                  unregisterCallback:id=>callbacks.delete(id),
                  invoke:async(cmd,args={})=>{
                    if(cmd==='backend_config')return {base_url:location.origin,token:'fixture-token'};
                    if(cmd==='plugin:event|listen'){const id=++sequence;listeners.set(id,args);return id;}
                    if(cmd==='plugin:event|unlisten'){listeners.delete(args.eventId);return;}
                    if(cmd==='pending_reminder')return pending;
                    if(cmd==='show_reminder_overlay'){pending=args.reminder;window.fixtureShows.push(pending.id);emit('reminder-updated',pending);return;}
                    if(cmd==='close_reminder_overlay'){pending=null;emit('reminder-cleared',null);return;}
                    if(cmd==='status_expanded'){window.fixtureResizes.push(args);return;}
                    if(cmd==='open_report_url'){window.fixtureLinks.push(args.url);return;}
                    throw Error('Unexpected native command '+cmd);
                  }
                };
                window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:(_event,id)=>listeners.delete(id)};
                localStorage.setItem('mavo-theme','dark');
            """)
            await context.route("**/api/v1/**", respond)
            page = await context.new_page()
            page.set_default_timeout(20_000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.clock.install()
            await page.goto(args.base_url.rstrip("/") + "/?overlay=status")
            await page.get_by_role("button", name="Mavo status", exact=True).wait_for()
            due.append(alert("First digest"))
            await page.clock.run_for(1100)
            await page.get_by_label("From First digest").wait_for()
            body = page.get_by_role("region", name="Notification contents")
            assert await body.locator(".report-list li").count() == 8
            assert await body.locator(".report-more").count() == 0
            assert await body.locator(".report").evaluate(
                "el=>getComputedStyle(el).fontFamily.includes('KaTeX_Main')"
            )
            assert "<br>" not in await body.inner_text()
            assert await body.get_by_role("button", name="Copy report as LaTeX").count() == 0
            assert await body.locator(".report-tools").count() == 0
            await body.get_by_role("link", name="Source", exact=True).first.click()
            assert await page.evaluate("window.fixtureLinks") == [
                "https://example.org/story/1"
            ]
            assert await body.evaluate("el=>el.scrollHeight>el.clientHeight")
            await body.focus()
            await page.keyboard.press("End")
            await page.clock.run_for(350)
            assert await body.evaluate("el=>el.scrollTop>0")
            assert await page.get_by_role(
                "button", name="Done", exact=True
            ).is_visible()
            await page.get_by_role("button", name="Full screen", exact=True).click()
            await page.locator('.toast-stage[data-reader="true"]').wait_for()
            assert await page.evaluate("window.fixtureResizes.at(-1).reader") is True
            # Native IPC is mocked: emulate its work-area resize for CSS checks.
            await page.set_viewport_size({"width": 1200, "height": 820})
            await page.clock.run_for(350)
            assert await body.locator(".report").evaluate(
                "el=>parseFloat(getComputedStyle(el).fontSize)>=16"
            )
            Path(".build").mkdir(exist_ok=True)
            await page.screenshot(path=".build/desktop-alert-reader.png")
            await page.get_by_role(
                "button", name="Restore notification size", exact=True
            ).click()
            await page.locator('.toast-stage[data-reader="false"]').wait_for()
            assert await page.evaluate("window.fixtureResizes.at(-1).reader") is False
            await page.set_viewport_size({"width": 440, "height": 420})
            await page.get_by_role("button", name="Full screen", exact=True).click()
            await page.locator('.toast-stage[data-reader="true"]').wait_for()
            due.append(alert("Second digest"))
            await page.clock.fast_forward(10_000)
            assert await page.evaluate("window.fixtureShows") == ["First digest"]
            # Reading mode holds later popups; Escape restores the compact card
            # without dismissing either update, then the pending one can appear.
            await page.keyboard.press("Escape")
            await page.locator('.toast-stage[data-reader="false"]').wait_for()
            assert await page.evaluate("window.fixtureResizes.at(-1).reader") is False
            await page.clock.run_for(1100)
            await page.get_by_label("From Second digest").wait_for()
            assert await page.evaluate("window.fixtureShows") == [
                "First digest",
                "Second digest",
            ]
            await page.get_by_role("button", name="Done", exact=True).click()
            await page.clock.run_for(1100)
            assert await page.evaluate("window.fixtureShows") == [
                "First digest",
                "Second digest",
            ]
            # A folded/expanded transition must not remount/replay the watcher.
            await page.get_by_role("button", name="Mavo status", exact=True).click()
            await page.get_by_role("button", name=re.compile(r"Missed\s*1")).click()
            await page.get_by_role(
                "button", name="First digest", exact=False
            ).wait_for()
            await page.clock.run_for(350)
            Path(".build").mkdir(exist_ok=True)
            await page.screenshot(path=".build/desktop-alert-ui.png")
            assert not errors, errors
            print(
                "PASS: eight readable items, escaped HTML breaks, no formatting controls, click-only source links, keyboard scrolling, full-screen/restore, queue isolation, delivery and Missed. Native IPC is simulated; zero credits."
            )
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
