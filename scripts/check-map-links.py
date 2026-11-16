"""Isolated graph visibility check. Run Vite on 1432 first; no account data or inference."""

import asyncio
from pathlib import Path

from mavo.desktop_actions import installed_browsers
from playwright.async_api import async_playwright


async def main():
    output = Path(__file__).resolve().parents[1] / ".build/map-links-check"
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=next(iter(installed_browsers().values())), headless=True
        )
        page = await browser.new_page(viewport={"width": 1680, "height": 1020})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.add_init_script("""window.__TAURI_INTERNALS__={
            invoke:async command=>command==='backend_config'?{base_url:location.origin,token:'fixture'}:null,
            transformCallback:()=>1,unregisterCallback:()=>{}};
            window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};""")

        async def api(route):
            assert route.request.method == "GET", "Graph checks must not change user data"
            path = route.request.url.split("/api/v1/")[-1].split("?")[0]
            if path == "routes":
                value = [{"id": key, "name": name, "provider": "airouter", "model": "fixture"}
                         for key, name in [("main", "Main Tasks"), ("spare", "Unassigned route")]]
            elif path == "pool":
                value = {"resources": [], "community": [], "playbooks": []}
            elif path == "auth/google":
                value = {"connected": False}
            else:
                value = []
            await route.fulfill(json=value)

        await page.route("**/api/v1/**", api)
        await page.goto("http://127.0.0.1:1432/preview.html?view=home&theme=dark&chat=0&tasks=0")
        await page.locator('.connection-row[data-connection-key="route:main"]').wait_for()

        async def populate(count):
            await page.evaluate("""async count => {
                const {useAppStore}=await import('/src/store/app.ts');
                const store=useAppStore.getState(), base=store.crew[0];
                useAppStore.setState({setupDrafts:[]});
                store.setCrew(Array.from({length:count},(_,i)=>({...base,id:'link-'+i,
                    name:['Inbox summary','Daily planner','News watch','Storage check'][i%4]+' '+i,
                    route_id:'main',agents:[],is_running:i%5!==4,status:'active',runtime_state:'waiting',
                    permissions:i%2===0?['google:gmail:read','notifications:show','action:gmail.draft']:[],
                    configuration_plan:{draft:{requested_google_resources:['gmail'],
                        requested_notifications:true,requested_actions:[{operation:'gmail.draft'}]}},
                    next_run_at:new Date(Date.now()+3600000).toISOString(),trigger_type:'interval',
                })));
            }""", count)
            await page.wait_for_function(
                "n=>document.querySelectorAll('.wire-route').length===n", arg=count
            )
            await page.mouse.move(1, 1)
            await page.wait_for_timeout(1000)

        async def visible_styles():
            return await page.locator('.wire-connection').evaluate_all("""els=>els.map(el=>{
                const s=getComputedStyle(el);return {kind:el.dataset.linkKind,on:el.dataset.on,
                    allowed:el.dataset.allowed,state:el.dataset.state,width:parseFloat(s.strokeWidth),
                    opacity:parseFloat(s.opacity),display:s.display,dashes:s.strokeDasharray,
                    vector:s.vectorEffect,animation:s.animationName,stroke:s.stroke};
            })""")

        for count in (2, 14, 100, 500):
            await populate(count)
            assert await page.locator('.wire-connection').count() == count * 4
            assert await page.locator('.wire-route[data-target="route:spare"]').count() == 0
            styles = await visible_styles()
            assert all(s["display"] != "none" and s["opacity"] == 1 for s in styles), styles[:4]
            assert all(s["width"] >= 1.5 and s["vector"] == "non-scaling-stroke" for s in styles)
            assert all(s["animation"] == "none" for s in styles), "No fake activity for idle Crew"
            if count == 14:
                granted = {s["kind"]: s for s in styles if s["allowed"] != "false" and s["state"] != "stopped"}
                assert granted["route"]["width"] > granted["resource"]["width"]
                assert granted["route"]["dashes"] == granted["resource"]["dashes"] == "none"
                assert granted["action"]["dashes"] != "none"
                for kind, alpha in [("route", "0.7"), ("resource", "0.56"), ("action", "0.6")]:
                    assert f"/ {alpha})" in granted[kind]["stroke"], granted[kind]
                await page.screenshot(path=str(output / "overview.png"))
            # A connection can be followed with keyboard, not just by hovering tiny paths.
            row = page.locator('.connection-row[data-connection-key="route:main"]')
            await row.focus()
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(550)
            assert await row.get_attribute("aria-pressed") == "true"
            assert await page.locator('.wire-route[data-on=true]').count() == count
            assert await page.locator('.wire-read[data-on=true]').count() == 0
            await page.wait_for_function("""()=>[...document.querySelectorAll('.wire-route[data-on=true]:not([data-state=stopped])')]
                .every(el=>parseFloat(getComputedStyle(el).strokeWidth)>=2.99&&getComputedStyle(el).opacity==='1')""")
            assert await page.locator('.wire-read').evaluate_all(
                "els=>els.every(el=>parseFloat(getComputedStyle(el).opacity)<=0.06)"
            ), "Unrelated resource paths must fade when tracing a model route"
            if count == 14:
                await page.screenshot(path=str(output / "route-trace.png"))
            await page.keyboard.press("Escape")
            await page.mouse.move(1, 1)
            await row.evaluate("el=>el.blur()")
            await page.wait_for_timeout(250)
            print(f"PASS: {count} Crew; real links visible; keyboard tracing; unused route has no edges")

        await populate(2)
        await page.locator('.map-preview').evaluate("el=>{el.style.width='800px';el.style.height='560px'}")
        await page.wait_for_timeout(650)
        assert all(s["width"] >= 1.5 for s in await visible_styles())
        # Pointer inspection uses the same authoritative selection as the dock button.
        await page.locator('.wire-route').first.dispatch_event('click')
        assert await page.locator('.connection-row[data-connection-key="route:main"]').get_attribute("aria-pressed") == "true"
        await page.screenshot(path=str(output / "compact-trace.png"))
        await page.emulate_media(reduced_motion="reduce")
        assert all(s["animation"] == "none" for s in await visible_styles())
        assert not errors, errors
        print("PASS: compact layout, clickable paths, reduced motion, zero real writes or model calls")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
