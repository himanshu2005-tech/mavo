"""Isolated browser checks for the model map; never touches a user's backend or accounts.

Run Vite on 1432, then: backend/.venv/Scripts/python scripts/check-model-map.py
"""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from mavo.desktop_actions import installed_browsers

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:1432"


async def main():
    output = ROOT / ".build" / "model-map-check"
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=next(iter(installed_browsers().values())), headless=True
        )
        page = await browser.new_page(viewport={"width": 1680, "height": 1020})
        errors, writes = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.add_init_script("""window.__TAURI_INTERNALS__ = {invoke: async command => {
            if (command === 'backend_config') return {base_url: location.origin, token: 'fixture'};
            throw Error('Native action is not available in this isolated check');
        }};""")

        async def api(route):
            request = route.request
            path = request.url.split("/api/v1/")[-1]
            if request.method == "PATCH" and path.startswith("crew/"):
                writes.append(path)
                crew_id = path.split("/")[-1]
                data = await page.evaluate("""async id => {
                    const {useAppStore} = await import('/src/store/app.ts');
                    return useAppStore.getState().crew.find(c => c.id === id);
                }""", crew_id)
                data.update(request.post_data_json)
                return await route.fulfill(json=data)
            if request.method == "POST" and path.endswith("/test"):
                writes.append(path)
                return await route.fulfill(json={"response": "OK", "model": "fixture"})
            if request.method != "GET":
                raise AssertionError("Unexpected write in isolated model map check")
            if path in {"memory", "tasks", "crew", "crew-drafts"}:
                data = []
            elif path.startswith("auth/google"):
                data = {"connected": False}
            elif path.startswith("crew/") and "/runs" not in path:
                data = {}
            else:
                data = []
            await route.fulfill(json=data)

        await page.route("**/api/v1/**", api)
        async def read_identity_colors():
            return await page.locator('.graph-agent[data-identity], .graph-connections .connection-row[data-identity]').evaluate_all("""nodes => Object.fromEntries(nodes.map(node => {
                const icon=node.querySelector('.map-face,.map-icon,.graph-route-icon');
                return [node.dataset.identity,getComputedStyle(icon).color];
            }))""")
        for theme in ("dark", "light"):
            await page.goto(f"{BASE}/preview.html?view=home&theme={theme}&chat=0&tasks=0")
            await page.locator(".graph-route").first.wait_for()
            assert await page.locator(".graph-route").count() == 2
            legend = page.locator('.graph-legend')
            assert await legend.is_visible()
            assert await legend.evaluate('el => getComputedStyle(el).opacity') == '1'
            resource = page.locator('.graph-node[data-kind=resource]').first
            action = page.locator('.graph-node[data-kind=action]').first
            provider = page.locator('.graph-route').first
            dock = page.locator('.graph-connections')
            assert await dock.count() == 1
            assert await dock.locator('.connection-group').count() == 3
            assert await dock.locator('[data-group=resource] .graph-node').count() > 0
            assert await dock.locator('[data-group=action] .graph-node').count() > 0
            assert await dock.locator('[data-group=route] .graph-route').count() == 2
            assert await page.locator('.graph-world .graph-node, .graph-world .graph-route').count() == 0
            assert await dock.evaluate('el => getComputedStyle(el).borderTopStyle') == 'solid'
            assert await page.locator('.flow-board').count() == 0
            assert await page.locator('.graph-canvas[data-readable]').count() == 0
            assert await page.locator('.graph-wires').first.evaluate(
                'el => getComputedStyle(el).visibility'
            ) == 'visible'
            # Verify actual node coordinates still use the original radial geometry.
            geometry = await page.evaluate("""async () => {
                const {radialLayout} = await import('/src/lib/radialGraph.ts');
                const elements=[...document.querySelectorAll('.graph-agent[data-identity]')];
                const ids=elements.map(el=>el.dataset.identity.slice('crew:'.length));
                // Shared hubs put the first agent on the 335-unit ring.
                const expected = radialLayout(ids, [{key:'route:test',users:ids}], 0);
                const el = elements[0];
                const world = document.querySelector('.graph-world');
                const center = [parseFloat(world.style.width)/2,parseFloat(world.style.height)/2];
                const point = expected.agents[ids[0]];
                return [parseFloat(el.style.left)-center[0],parseFloat(el.style.top)-center[1],point.x-expected.c.x,point.y-expected.c.y];
            }""")
            assert abs(geometry[0]-geometry[2]) < 0.01
            assert abs(geometry[1]-geometry[3]) < 0.01
            await page.wait_for_timeout(1600)  # let the entrance animation finish
            assert await page.locator('.graph-agent:not(.graph-new) b').first.evaluate(
                'el => getComputedStyle(el).fontSize'
            ) == '14px'
            assert await page.locator('.graph-route-text b').first.evaluate(
                'el => getComputedStyle(el).fontSize'
            ) == '13px'
            assert await page.locator('.graph-canvas[data-large]').count() == 0
            ready_card=page.locator(".graph-agent[title^='Inbox Summary']").first
            busy_card=page.locator('.graph-agent[data-activity=working]').first
            assert await ready_card.get_attribute('data-activity') == 'ready'
            assert await ready_card.locator('.agent-ring-motion').evaluate('el => getComputedStyle(el).animationDuration') == '6s'
            assert await busy_card.locator('.agent-ring-motion').evaluate('el => getComputedStyle(el).animationDuration') == '1.15s'
            identity_colors = await read_identity_colors()
            assert len(set(identity_colors.values())) == len(identity_colors), identity_colors
            saved = await page.evaluate("() => JSON.parse(localStorage.getItem('mavo-identity-colors-v1'))")
            assert saved['version'] == 1
            await page.reload()
            await page.locator('.graph-connections .graph-route').first.wait_for()
            await page.wait_for_timeout(900)
            assert await read_identity_colors() == identity_colors
            mismatches = await page.locator('.wire[data-identity]').evaluate_all("""paths => paths.flatMap(path => {
                const host=[...document.querySelectorAll('.graph-agent[data-identity], .connection-row[data-identity]')].find(el => el.dataset.identity === path.dataset.identity);
                const actual=getComputedStyle(path).getPropertyValue('--identity');
                const expected=host&&getComputedStyle(host).getPropertyValue('--identity');
                return !host||actual===expected?[]:[{key:path.dataset.identity,actual,expected}];
            })""")
            assert not mismatches, mismatches
            # Endpoints remain attached to the box after both scroll and camera zoom.
            async def check_endpoint():
                errors = await page.evaluate("""() => {
                    const panel = document.querySelector('.graph-connections').getBoundingClientRect();
                    const body = document.querySelector('.connections-body').getBoundingClientRect();
                    return [...document.querySelectorAll('.graph-connections [data-connection-key]')].map(row => {
                        const path = [...document.querySelectorAll('path[data-target]')].find(p => p.dataset.target === row.dataset.connectionKey);
                        if (!path) return 0;
                        const end = path.getPointAtLength(path.getTotalLength());
                        const point = new DOMPoint(end.x,end.y).matrixTransform(path.getScreenCTM());
                        const rect = row.getBoundingClientRect();
                        const y = Math.max(body.top+8, Math.min(body.bottom-8,(rect.top+rect.bottom)/2));
                        return Math.max(Math.abs(point.x-panel.right-2),Math.abs(point.y-y));
                    });
                }""")
                assert max(errors) < 2, errors
            await check_endpoint()
            camera = await page.locator('.graph-world').evaluate('el => el.style.transform')
            dock_bounds = await dock.bounding_box()
            await page.mouse.move(dock_bounds['x']+80,dock_bounds['y']+180)
            await page.mouse.wheel(0,650)
            await page.wait_for_timeout(300)
            assert await page.locator('.graph-world').evaluate('el => el.style.transform') == camera
            await check_endpoint()
            await page.locator('.connections-body').evaluate('el => el.scrollTop=0')
            await page.mouse.move(1,1)
            await page.wait_for_timeout(200)
            await page.screenshot(path=str(output / f"{theme}-categories.png"))
            await page.locator(".graph-agent[title^='Inbox Summary']").first.click()
            await page.wait_for_function("document.querySelectorAll('.trace-dot').length === 4")
            assert await read_identity_colors() == identity_colors
            kinds = await page.locator(".trace-dot").evaluate_all(
                "dots => dots.map(dot => dot.dataset.kind)"
            )
            assert kinds == ["you", "think", "read", "act"]
            await page.wait_for_timeout(1200)
            await page.screenshot(path=str(output / f"{theme}-agent.png"))
            await page.locator(".graph-route").filter(has_text="Ollama").click()
            fallback = page.get_by_label("Cloud fallback for Inbox Summary", exact=True)
            await fallback.wait_for()
            assert await fallback.input_value() == ""
            assert "permitted data" in await page.locator(".graph-side").inner_text()
            await page.get_by_role("button", name="Test connection", exact=True).click()
            await page.get_by_role("status").filter(has_text="Model replied successfully").wait_for()
            await fallback.select_option("r")
            await page.wait_for_function("""async () => {
                const {useAppStore} = await import('/src/store/app.ts');
                return useAppStore.getState().crew.find(c => c.id === 'inbox').fallback_route_id === 'r';
            }""")
            await fallback.select_option("")
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(output / f"{theme}-model.png"))
            panel = await page.locator(".graph-side").bounding_box()
            assert panel and panel["x"] >= 0 and panel["x"] + panel["width"] <= 1680
            # Real execution is separate from persistent enabled state and hover previews.
            async def feed_snapshot(cursor,events,runs,calls):
                await page.evaluate("""async value => {
                    const {useAppStore}=await import('/src/store/app.ts');
                    useAppStore.getState().setExecution(value);
                }""",{'stream_id':'execution-fixture','cursor':cursor,'reset':False,'events':events,'active_runs':runs,'active_calls':calls})
            run={'seq':1,'kind':'run_started','at':'2026-11-08T06:00:00Z','crew_id':'inbox','run_id':'fixture-run','route_id':'local'}
            call={**run,'seq':2,'kind':'model_started','call_id':'fixture-call','provider':'ollama','model':'qwen2.5:3b'}
            await feed_snapshot(1,[run],[run],[])
            assert await page.locator('.provider-request-pulse').count() == 0
            assert await page.locator('.connection-row[data-execution=requesting]').count() == 0
            await feed_snapshot(2,[call],[run],[call])
            await page.locator('.provider-request-pulse').wait_for(state='attached')
            assert await page.locator('.wire-route[data-provider-active=true]').count() == 1
            assert await page.locator('.connection-row[data-execution=requesting]').count() == 1
            assert 'Requesting' in await page.locator('.connection-row[data-execution=requesting]').inner_text()
            assert 'Model request in progress' in await page.locator('.graph-run-notices').inner_text()
            await page.screenshot(path=str(output/f'{theme}-actual-model-request.png'))
            response={**call,'seq':3,'kind':'model_finished','status':'responded'}
            finished={**run,'seq':4,'kind':'run_finished','status':'completed'}
            await feed_snapshot(4,[response,finished],[],[])
            assert await page.locator('.provider-request-pulse').count() == 0
            assert await page.locator('.connection-row[data-execution=responded]').count() == 1
            assert 'Run completed' in await page.locator('.graph-run-notices').inner_text()
            await page.screenshot(path=str(output/f'{theme}-actual-run-completed.png'))
            # An entire request can finish between refreshes; completion remains noticeable.
            await feed_snapshot(8,[{**run,'seq':5},{**call,'seq':6},{**response,'seq':7},{**finished,'seq':8}],[],[])
            assert await page.locator('.provider-request-pulse').count() == 0
            assert 'Run completed' in await page.locator('.graph-run-notices').inner_text()
            await page.evaluate("""async () => {
                const {useAppStore}=await import('/src/store/app.ts');
                useAppStore.setState({execution:null,executionError:null});
            }""")
            # Stop only a synthetic agent, preserving all real workspace state.
            await page.evaluate("""async () => {
                const {useAppStore} = await import('/src/store/app.ts');
                const store = useAppStore.getState();
                store.upsertCrew({...store.crew.find(c => c.id === 'inbox'),
                    is_running: false, runtime_state: 'working'});
            }""")
            card = page.locator(".graph-agent[title^='Inbox Summary']").first
            await card.click()
            await page.wait_for_function("document.querySelectorAll('.trace-dot').length === 0")
            assert await card.get_attribute("data-state") == "stopped"
            assert await card.locator('.agent-state-ring').count() == 0
            await page.screenshot(path=str(output / f"{theme}-stopped.png"))
            # Running again restores the same card and its regular preview without a reload.
            await page.evaluate("""async () => {
                const {useAppStore} = await import('/src/store/app.ts');
                const store = useAppStore.getState();
                store.upsertCrew({...store.crew.find(c => c.id === 'inbox'),
                    is_running: true, runtime_state: 'waiting'});
            }""")
            await page.mouse.move(1, 1)
            await page.wait_for_function("document.querySelectorAll('.trace-dot').length === 4")
            assert await card.locator('.agent-state-ring').count() == 1
            # Budget/browser waits are not presented as actual worker execution.
            for runtime_state,label in [('budget_paused','Back tomorrow'),('waiting_browser','Waiting for browser')]:
                await page.evaluate("""async state => {
                    const {useAppStore}=await import('/src/store/app.ts');
                    const store=useAppStore.getState();
                    store.upsertCrew({...store.crew.find(c=>c.id==='inbox'),runtime_state:state});
                }""",runtime_state)
                assert await card.get_attribute('data-activity') == 'waiting'
                assert await card.locator('.agent-state-ring').count() == 0
                assert label in await card.inner_text()
            await page.evaluate("""async () => {
                const {useAppStore}=await import('/src/store/app.ts');
                const store=useAppStore.getState();
                store.upsertCrew({...store.crew.find(c=>c.id==='inbox'),runtime_state:'working'});
            }""")
            assert await card.get_attribute('data-activity') == 'working'
            assert await card.locator('.agent-ring-motion').evaluate('el => getComputedStyle(el).animationDuration') == '1.15s'
            await page.evaluate("""async () => {
                const {useAppStore}=await import('/src/store/app.ts');
                const store=useAppStore.getState();
                store.upsertCrew({...store.crew.find(c=>c.id==='inbox'),runtime_state:'waiting'});
            }""")
        await page.emulate_media(reduced_motion="reduce")
        await page.locator(".graph-agent[title^='Inbox Summary']").first.click()
        hidden = await page.locator(".trace-dot").evaluate_all(
            "dots => dots.every(dot => getComputedStyle(dot).display === 'none')"
        )
        assert hidden
        assert await page.locator('.agent-ring-motion').evaluate_all(
            "els => els.every(el => getComputedStyle(el).animationName === 'none')"
        )
        # The restored original camera adapts when the window changes, without packing cards.
        for width, height in ((1920, 1080), (1280, 800), (920, 640)):
            await page.set_viewport_size({"width": width, "height": height})
            await page.locator('.map-preview').evaluate("""(el, size) => {
                el.style.width = (size[0] - 80) + 'px';
                el.style.height = (size[1] - 80) + 'px';
            }""", [width, height])
            await page.wait_for_timeout(350)
            selected = page.locator('.graph-agent[aria-pressed="true"]')
            bounds = await selected.bounding_box()
            panel = await page.locator('.graph-side').bounding_box()
            assert bounds and panel
            key_bounds = await legend.bounding_box()
            assert key_bounds and key_bounds['x'] + key_bounds['width'] <= panel['x']
            assert await page.locator('.graph-canvas[data-readable]').count() == 0
            assert await selected.locator('b').evaluate(
                'el => getComputedStyle(el).fontSize'
            ) == '14px'
            await page.screenshot(path=str(output / f'handoff-{width}.png'))
        # Keep the original fit/auto controls, not the redesigned overview mode.
        await page.get_by_role('button', name='Zoom in', exact=True).click()
        await page.wait_for_timeout(600)
        await check_endpoint()
        await page.get_by_role('button', name='Fit to screen', exact=True).click()
        await page.wait_for_timeout(200)
        # A small team keeps the original graph styling too (fixture-only edits).
        await page.evaluate("""async () => {
            const {useAppStore} = await import('/src/store/app.ts');
            const store = useAppStore.getState();
            store.setCrew(store.crew.slice(0,2));
            store.setSetupDrafts([]);
        }""")
        await page.wait_for_timeout(400)
        assert await page.locator('.graph-agent:not(.graph-new)').count() == 2
        assert await page.locator('.graph-canvas[data-large]').count() == 0
        await page.screenshot(path=str(output / 'original-style-two-agents.png'))
        before_colors = await read_identity_colors()
        await page.evaluate("""async () => {
            const {useAppStore}=await import('/src/store/app.ts');
            const store=useAppStore.getState();
            store.setCrew([...store.crew].reverse());
        }""")
        await page.wait_for_timeout(200)
        assert await read_identity_colors() == before_colors
        await page.evaluate("""async () => {
            const {useAppStore}=await import('/src/store/app.ts');
            const store=useAppStore.getState();
            store.setCrew([...store.crew,{...store.crew[0],id:'color-fixture-new',name:'Color fixture'}]);
        }""")
        await page.wait_for_timeout(250)
        after_colors = await read_identity_colors()
        for key,color in before_colors.items():
            assert after_colors[key] == color
        assert len(set(after_colors.values())) == len(after_colors)
        assert not errors, errors
        assert len(writes) == 6  # two tests + four policy edits, all mocked
        print(json.dumps({"presentation": "graph-grouped-connections", "categories": ["resources", "actions", "model routes"], "themes": 2, "routes": 2, "trace": kinds,
                          "identity_colors": "unique in both themes", "identity_stability": "reload, reorder, addition",
                          "activity": "running, working, waiting, stopped, reduced motion",
                          "fallback_default": "off", "reduced_motion": "passed",
                          "real_backend_writes": 0, "console_errors": errors}))
        await browser.close()


asyncio.run(main())
