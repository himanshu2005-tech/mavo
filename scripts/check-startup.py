"""Exercise the real frontend entry before login, after login, and on module-load failure.

Use a local Vite server on 1420. All API replies are isolated fixtures; no account changes.
"""
import asyncio
import json

from playwright.async_api import async_playwright

from mavo.desktop_actions import installed_browsers


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=next(iter(installed_browsers().values())), headless=True
        )
        for signed_in in (False, True):
            page = await browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.add_init_script("""window.__TAURI_INTERNALS__ = {
                invoke: async command => {
                    if (command === 'backend_config') return {base_url: location.origin, token: 'fixture'};
                    if (command === 'plugin:event|listen') return 1;
                    return null;
                }, transformCallback: () => 1, unregisterCallback: () => {}
            }; window.__TAURI_EVENT_PLUGIN_INTERNALS__ = {unregisterListener: () => {}};""")

            async def api(route):
                assert route.request.method == 'GET', 'Startup must not mutate accounts'
                path = route.request.url.split('/api/v1/')[-1]
                if path == 'auth/firebase':
                    data = {'configured': True, 'signed_in': signed_in, 'pending': None,
                            'email': None, 'error': None}
                elif path == 'status':
                    data = {'service': 'mavo', 'version': 'test', 'database': 'connected', 'providers': []}
                elif path == 'auth/google':
                    data = {'connected': False}
                elif path.startswith('runtime/activity'):
                    data = {'stream_id':'fixture','cursor':0,'reset':False,'events':[], 'active_runs':[], 'active_calls':[]}
                else:
                    data = []
                await route.fulfill(json=data)

            await page.route('**/api/v1/**', api)
            await page.goto('http://localhost:1420/')
            if signed_in:
                await page.locator('.graph-canvas').wait_for(timeout=20_000)
            else:
                await page.get_by_role('button', name='Sign in', exact=True).wait_for(timeout=20_000)
                assert await page.get_by_role('heading', name='Welcome to Mavo').is_visible()
            assert not errors, errors
            assert await page.locator('#mavo-startup').count() == 0
            await page.close()
        # A module that cannot load must offer recovery, never a blank white root.
        page = await browser.new_page()
        # Vite appends a timestamp after HMR; abort that module too, not just a bare URL.
        await page.route('**/src/main.tsx*', lambda route: route.abort())
        await page.goto('http://localhost:1420/')
        await page.get_by_role('button', name='Reload Mavo', exact=True).wait_for()
        assert await page.get_by_role('alert').is_visible()
        assert await page.locator('body').evaluate('el => getComputedStyle(el).backgroundColor') == 'rgb(16, 16, 16)'
        print(json.dumps({'signed_out_login': 'passed', 'signed_in_workspace': 'passed',
                          'broken_import_recovery': 'passed', 'real_account_changes': 0}))
        await browser.close()


asyncio.run(main())
