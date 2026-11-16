"""First-use browser regression checks. All accounts, routes and mutations are isolated fixtures."""

import asyncio
import json
from pathlib import Path

from mavo.desktop_actions import installed_browsers
from mavo.google_permissions import CAPABILITIES
from playwright.async_api import async_playwright

BASE = 'http://127.0.0.1:1432'
ROOT = Path(__file__).resolve().parents[1]


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=next(iter(installed_browsers().values())), headless=True
        )
        context = await browser.new_context(viewport={'width': 1280, 'height': 900})
        page = await context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await context.add_init_script("""window.__TAURI_INTERNALS__ = {
            invoke: async command => command==='backend_config'?{base_url:location.origin,token:'fixture'}:1,
            transformCallback:()=>1, unregisterCallback:()=>{}
        };window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
        window.__locationReads=0;
        navigator.geolocation.getCurrentPosition=()=>{window.__locationReads++;};
        localStorage.setItem('mavo-theme','dark');""")
        state = {'version': 1, 'step': 'welcome', 'revision': 0, 'account_key': 'first-account',
                 'completed_at': None, 'providers_skipped': False}
        routes = []
        writes = []
        failed = False
        signed_in = True

        async def respond(route):
            nonlocal failed, signed_in
            request = route.request
            path = request.url.split('/api/v1/')[-1].split('?')[0]
            if path == 'auth/firebase':
                if request.method == 'DELETE':
                    signed_in = False
                    writes.append((path, 'logout'))
                    await route.fulfill(status=204)
                    return
                value = {'configured': True, 'signed_in': signed_in, 'phase': 'idle',
                         'email': None, 'name': None, 'error': None}
            elif path == 'onboarding':
                if failed:
                    await route.fulfill(status=503, json={'detail': 'Setup temporarily unavailable'})
                    return
                if request.method == 'PUT':
                    body = request.post_data_json
                    assert body['revision'] == state['revision']
                    writes.append((path, body['action']))
                    steps = ['welcome', 'providers', 'resources', 'complete']
                    index = steps.index(state['step'])
                    state['step'] = steps[index - 1 if body['action'] == 'back' else index + 1]
                    state['revision'] += 1
                    if body['action'] == 'skip':
                        state['providers_skipped'] = True
                    if state['step'] == 'complete':
                        state['completed_at'] = '2026-11-08T00:00:00Z'
                value = dict(state)
            elif path == 'routes':
                if request.method == 'POST':
                    payload = request.post_data_json
                    assert payload['provider'] == 'ollama'
                    assert not payload.get('api_key')
                    routes.append({**payload, 'id': 'test-route', 'has_credential': True,
                                   'created_at': 'now', 'updated_at': 'now'})
                    writes.append((path, 'create'))
                    await route.fulfill(status=201, json=routes[-1])
                    return
                value = list(routes)
            elif path == 'status':
                value = {'service': 'mavo', 'version': 'test', 'database': 'connected',
                         'providers': [{'id': 'ollama', 'name': 'Ollama', 'models': [],
                                        'default_model': 'qwen-test', 'local': True,
                                        'configured': True, 'connected': True, 'error': None}]}
            elif path == 'resources/access':
                value = {'weather': False, 'location': False}
            elif path == 'auth/google':
                value = {'configured': True, 'signed_in': False, 'phase': 'idle',
                         'capabilities': [], 'error': None}
            elif path == 'auth/google/start':
                assert request.method == 'POST'
                capabilities = request.post_data_json['capabilities']
                assert len(capabilities) == len(CAPABILITIES)
                assert set(capabilities) == set(CAPABILITIES)
                writes.append((path, 'request'))
                value = {'authorization_url': 'https://accounts.google.com/o/oauth2/v2/auth?fixture=1'}
            elif path.startswith('runtime/activity'):
                value = {'stream_id': 'fixture', 'cursor': 0, 'reset': False,
                         'events': [], 'active_runs': [], 'active_calls': []}
            else:
                assert request.method == 'GET', 'Unexpected write during setup'
                value = []
            await route.fulfill(json=value)

        await context.route('**/api/v1/**', respond)
        output = ROOT / '.build/onboarding-check'
        output.mkdir(parents=True, exist_ok=True)
        await page.goto(BASE)
        await page.get_by_role('heading', name='Welcome to Mavo', exact=True).wait_for()
        panel = await page.locator('.onboarding-shell').bounding_box()
        assert panel and panel['width'] <= 820 and panel['height'] <= 610
        assert await page.locator('.onboarding h1').evaluate(
            'el => parseFloat(getComputedStyle(el).fontSize)'
        ) <= 22
        assert await page.locator('.graph-canvas').count() == 0
        await page.screenshot(path=str(output/'welcome.png'))
        await page.get_by_role('button', name='Continue', exact=True).click()
        await page.get_by_role('heading', name='Model providers', exact=True).wait_for()
        assert await page.get_by_role('button', name='Continue', exact=True).is_disabled()
        await page.reload()
        await page.get_by_role('heading', name='Model providers', exact=True).wait_for()
        await page.get_by_role('button', name='Add a provider', exact=True).click()
        await page.get_by_label('Route name', exact=True).fill('Local intelligence')
        await page.get_by_label('Provider', exact=True).select_option('ollama')
        await page.get_by_label('Exact model ID', exact=True).fill('qwen-test')
        await page.get_by_role('button', name='Save route', exact=True).click()
        await page.get_by_role('heading', name='Local intelligence').wait_for()
        await page.screenshot(path=str(output/'providers.png'))
        await page.get_by_role('button', name='Continue', exact=True).click()
        await page.get_by_role('heading', name='Resources', exact=True).wait_for()
        assert await page.get_by_role('switch', name='Weather access').get_attribute('aria-checked') == 'false'
        assert await page.get_by_role('switch', name='Location access').get_attribute('aria-checked') == 'false'
        assert await page.evaluate('window.__locationReads') == 0
        await page.get_by_text('Review requested access', exact=True).click()
        assert await page.get_by_text('YouTube — read subscriptions, their videos and your liked videos.').is_visible()
        await page.get_by_role('button', name='Connect Google', exact=True).click()
        await page.wait_for_function("""[...document.querySelectorAll('button')].some(
            el => el.textContent.trim()==='Connect Google'&&!el.disabled)""")
        await page.screenshot(path=str(output/'resources.png'))
        await page.get_by_role('button', name='Finish', exact=True).click()
        await page.locator('.graph-canvas').wait_for()
        await page.reload()
        await page.locator('.graph-canvas').wait_for()
        assert state['step'] == 'complete'
        assert writes == [('onboarding', 'next'), ('routes', 'create'),
                          ('onboarding', 'next'), ('auth/google/start', 'request'),
                          ('onboarding', 'next')]
        # A different account has its own first-use state; an empty route list is not "connected".
        state.update(step='welcome', revision=0, completed_at=None,
                     providers_skipped=False, account_key='second-account')
        routes.clear()
        await page.set_viewport_size({'width': 390, 'height': 844})
        await page.reload()
        await page.get_by_role('button', name='Continue', exact=True).click()
        await page.get_by_role('button', name='Set up later', exact=True).click()
        await page.get_by_role('heading', name='Resources', exact=True).wait_for()
        await page.screenshot(path=str(output/'compact-resources.png'))
        assert await page.locator('.onboarding').evaluate(
            'el => el.scrollWidth<=el.clientWidth+1'
        )
        await page.get_by_role('button', name='Finish', exact=True).click()
        await page.locator('.graph-canvas').wait_for()
        assert state['providers_skipped']
        assert routes == []
        # Loading failure blocks workspace access and offers a real retry.
        failed = True
        await page.reload()
        await page.get_by_role('button', name='Retry', exact=True).wait_for()
        assert await page.locator('.graph-canvas').count() == 0
        failed = False
        await page.get_by_role('button', name='Retry', exact=True).click()
        await page.locator('.graph-canvas').wait_for()
        state.update(step='welcome',revision=0,completed_at=None,account_key='third-account')
        await page.reload()
        await page.get_by_role('button',name='Sign out',exact=True).click()
        await page.get_by_role('button',name='Sign in',exact=True).wait_for()
        assert await page.locator('.graph-canvas, .onboarding').count() == 0
        assert not errors, errors
        print(json.dumps({'welcome': 'passed', 'provider_save': 'passed', 'resume': 'passed',
                          'optional_resources': 'passed', 'explicit_skip': 'passed',
                          'account_progress_isolation': 'passed', 'compact_layout': 'passed',
                          'failure_retry': 'passed', 'setup_sign_out': 'passed',
                          'google_full_capability_request': 'passed',
                          'implicit_access_grants': 0,
                          'real_account_changes': 0, 'console_errors': errors}))
        await browser.close()


asyncio.run(main())
