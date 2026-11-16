"""Verify saved connections remain visible with zero Crew, using isolated HTTP fixtures."""

import asyncio
from pathlib import Path

from mavo.desktop_actions import installed_browsers
from playwright.async_api import async_playwright


def resource(key, name, kind="google", **over):
    return dict(id=key, name=name, group="Connected", kind=kind, description=name,
                connected=True, account=None, level="read", can_act=False,
                reads=[f"{key}:read"], readable=[f"{key}:read"], actions=[], **over)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=next(iter(installed_browsers().values())), headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        await context.add_init_script("""window.__TAURI_INTERNALS__={invoke:async command=>command==='backend_config'?{base_url:location.origin,token:'fixture'}:1,transformCallback:()=>1,unregisterCallback:()=>{}};
            window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
            localStorage.setItem('mavo-theme','dark');localStorage.setItem('mavo-chat-open','0');""")
        routes = [{"id": "local", "name": "Ollama", "provider": "ollama", "model": "fixture", "has_credential": True}]
        resources = [resource("gmail", "Gmail"), resource("calendar", "Calendar"),
                     resource("weather", "Weather", "toggle"), resource("location", "Location", "toggle")]
        async def respond(route):
            request = route.request
            assert request.method == "GET", "This check must not mutate the workspace"
            path = request.url.split('/api/v1/')[-1].split('?')[0]
            if path == 'auth/firebase':
                value = dict(configured=True, signed_in=True, phase="idle", name="Fixture", error=None)
            elif path == 'onboarding':
                value = dict(version=1, step="complete", revision=3, account_key="fixture", completed_at="2026-11-08T00:00:00Z", providers_skipped=False)
            elif path == 'status':
                value = dict(service="mavo", version="fixture", database="connected", providers=[])
            elif path == 'routes':
                value = routes
            elif path == 'pool':
                value = dict(resources=resources, community=[], playbooks=[])
            elif path == 'auth/google':
                value = dict(configured=True, signed_in=True, phase="idle", capabilities=["gmail:read", "calendar:read"])
            elif path.startswith('runtime/activity'):
                value = dict(stream_id="fixture", cursor=0, reset=False, events=[], active_runs=[], active_calls=[])
            else:
                value = []
            await route.fulfill(json=value)
        await context.route('**/api/v1/**', respond)
        page = await context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto('http://127.0.0.1:1432')
        panel = page.get_by_role('region', name='Connections', exact=True)
        await panel.get_by_role('button', name='Ollama', exact=False).wait_for()
        for name in ['Gmail', 'Calendar', 'Weather', 'Location']:
            await panel.locator('.map-label').get_by_text(name, exact=True).wait_for()
        assert await page.locator('.graph-agent[data-identity]').count() == 0
        assert await page.locator('.wire-read,.wire-act,.wire-route').count() == 0
        assert await panel.locator('.connection-row').count() == 5
        assert 'Not assigned' in await panel.inner_text()
        assert 'No enabled actions.' in await panel.inner_text()
        assert 'No resources in use' not in await panel.inner_text()
        # An idle connection is inspectable, even without an agent.
        await panel.locator('.map-label').get_by_text('Gmail', exact=True).click()
        await page.get_by_role('heading', name='Gmail', exact=True).wait_for()
        assert not errors, errors
        output = Path(__file__).resolve().parents[1] / '.build/connections-check'
        output.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(output / 'unassigned.png'))
        print('PASS: 1 saved route + 4 connected resources, zero Crew, zero phantom edges; idle connection inspection works.')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
