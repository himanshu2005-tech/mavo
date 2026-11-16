r"""Browser interaction regression for real chat components, using isolated HTTP fixtures.

Run Vite on 1432, then backend\.venv\Scripts\python scripts\check-orchestration.py.
No real accounts, model credits or desktop actions are used.
"""

import asyncio
from copy import deepcopy
from pathlib import Path

from mavo.desktop_actions import installed_browsers
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
CHAT = "11111111-1111-4111-8111-111111111111"


def crew(key, name):
    return {"id": key, "name": name, "template_id": "research", "responsibilities": "Research",
            "provider": "ollama", "model": "fixture", "route_id": "local", "status": "active",
            "autonomy_mode": "reactive", "permissions": ["model:invoke"], "resources": [],
            "triggers": [], "trigger_type": "manual", "is_running": False,
            "runtime_state": "stopped", "review_interval_minutes": 5, "max_model_calls": 4,
            "daily_call_limit": 20, "daily_calls_used": 0, "configuration_plan": {},
            "observation": {}, "created_at": "2026-11-08T00:00:00Z", "updated_at": "2026-11-08T00:00:00Z"}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=next(iter(installed_browsers().values())), headless=True)
        context = await browser.new_context(viewport={"width": 1360, "height": 960}, reduced_motion="reduce")
        await context.add_init_script("""window.__TAURI_INTERNALS__={invoke:async command=>command==='backend_config'?{base_url:location.origin,token:'fixture'}:1,transformCallback:()=>1,unregisterCallback:()=>{}};
            window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
            localStorage.setItem('mavo-theme','dark');localStorage.setItem('mavo-chat-open','1');""")
        rows = [crew("research", "Research Crew"), crew("writing", "Writing Crew"), crew("computer", "Computer Crew")]
        rows[0]["agents"] = [
            {"id":"research-discovery", "crew_id":"research", "key":"discovery", "name":"Discovery", "role":"Find sources",
             "instructions":"Read authorized sources", "route_id":None, "status":"active", "runtime_state":"waiting", "checkpoint":None,
             "definition":{"depends_on":[], "capabilities":[], "completion_criteria":"Cite actual source evidence"}, "execution":{}},
            {"id":"research-matching", "crew_id":"research", "key":"matching", "name":"Matching", "role":"Check suitability",
             "instructions":"Compare findings", "route_id":None, "status":"active", "runtime_state":"waiting", "checkpoint":None,
             "definition":{"depends_on":["discovery"], "capabilities":[], "completion_criteria":"Explain eligibility"}, "execution":{}},
        ]
        chat = {"id": CHAT, "session_id": CHAT, "title": "Workspace", "messages": [],
                "created_at": "2026-11-08T00:00:00Z", "updated_at": "2026-11-08T00:00:00Z"}
        events = []
        for i, key in enumerate(("research", "writing")):
            events.append({"id": f"question-{i}", "seq": i+1, "kind": "question", "sender": f"crew:{key}",
                "crew_id": key, "task_id": "task", "step_id": key, "status": "pending", "revision": 0,
                "payload": {"summary": f"Which {key} topic?"}, "created_at": "2026-11-08T00:00:00Z"})
        events.append({"id": "permission", "seq": 3, "kind": "permission_request", "sender": "crew:computer",
            "crew_id": "computer", "task_id": None, "step_id": None, "status": "pending", "revision": 0,
            "payload": {"summary": "Read computer counters", "capability": {"capability": "device:read",
                "operation": None, "scope": {}, "risk": "read", "duration": "crew", "setup": "grant",
                "explanation": "Read counters"}}, "created_at": "2026-11-08T00:00:00Z"})
        edit = {"id": "edit", "crew_id": "research", "scope": "persistent", "status": "proposed",
                "base_version": 0, "version": None, "diff": [{"field": "review_interval_minutes",
                "label": "Check interval (minutes)", "before": 5, "after": 10}]}
        events.append({"id": "configuration", "seq": 4, "kind": "configuration", "sender": "core",
            "crew_id": "research", "task_id": None, "step_id": None, "status": "recorded", "revision": 0,
            "payload": {"summary": "Review the interval", "edit": edit}, "created_at": "2026-11-08T00:00:00Z"})
        writes, errors = [], []
        async def respond(route):
            request = route.request
            path = request.url.split('/api/v1/')[-1].split('?')[0]
            method = request.method
            if method != 'GET':
                writes.append((path, request.post_data_json))
            if path == 'auth/firebase':
                value = {"configured": True, "signed_in": True, "phase": "idle", "name": "Fixture", "error": None}
            elif path == 'onboarding':
                value = {"version": 1, "step": "complete", "revision": 3, "account_key": "fixture", "completed_at": "2026-11-08T00:00:00Z", "providers_skipped": False}
            elif path == 'status':
                value = {"service": "mavo", "version": "fixture", "database": "connected", "providers": []}
            elif path == 'crew':
                value = rows
            elif path == 'routes':
                value = [{"id": "local", "name": "Local", "provider": "ollama", "model": "fixture", "has_credential": True}]
            elif path == 'core/status':
                value = {"configured": True, "connected": True, "provider": "Mavo Core", "model": "fixture", "error": None}
            elif path == 'core/conversations':
                value = [chat]
            elif path == 'core/turn':
                chat['messages'] = [{"id": "u", "role": "user", "content": request.post_data_json['message'], "created_at": "2026-11-08T00:00:01Z"},
                    {"id": "a", "role": "assistant", "content": "Working together.", "created_at": "2026-11-08T00:00:02Z"}]
                value = {"reply": "Working together.", "conversation": chat, "crews": [], "crew": None, "draft": None}
            elif path == 'orchestration/events':
                value = deepcopy(events)
            elif path.startswith('orchestration/events/') and path.endswith('/answer'):
                event_id = path.split('/')[2]
                row = next(e for e in events if e['id'] == event_id)
                row['status'] = 'resolved'
                row['revision'] += 1
                value = {"resolved": True}
            elif path == 'configuration/edit/apply':
                edit['status'] = 'applied'
                edit['version'] = 1
                value = edit
            elif path == 'auth/google':
                value = {"configured": True, "signed_in": False, "phase": "idle", "capabilities": []}
            elif path == 'resources/access':
                value = {"weather": False, "location": False}
            elif path.startswith('runtime/activity'):
                value = {"stream_id": "fixture", "cursor": 0, "reset": False, "events": [], "active_runs": [], "active_calls": []}
            else:
                assert method == 'GET', f"Unexpected write: {path}"
                value = []
            await route.fulfill(json=value)
        await context.route('**/api/v1/**', respond)
        page = await context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto('http://127.0.0.1:1432')
        field = page.get_by_role('textbox', name='Message Mavo', exact=True)
        try:
            await field.wait_for(timeout=10000)
        except Exception:
            print('Browser diagnostics:', errors, (await page.locator('body').inner_text())[:3000])
            raise
        await page.locator('#answer-question-0').fill('Internships')
        await page.locator('#answer-question-0').locator('..').get_by_role('button', name='Reply', exact=True).click()
        assert writes[-1][0] == 'orchestration/events/question-0/answer'
        assert writes[-1][1]['answer'] == 'Internships'
        await page.locator('#answer-question-1').wait_for()
        await page.get_by_role('article').filter(has_text='Read computer counters').get_by_role('switch').click()
        assert any(path == 'orchestration/events/permission/answer' and body['decision'] == 'grant' for path, body in writes)
        await page.get_by_role('button', name='Apply changes', exact=True).click()
        await page.get_by_text('5 → 10', exact=True).wait_for()
        await field.fill('@rscr')
        await page.get_by_role('listbox', name='Your Crew').wait_for()
        await field.press('Enter')
        await field.fill((await field.input_value())+'@Writing')
        await field.press('Enter')
        await field.fill((await field.input_value())+'work together')
        await page.get_by_role('button', name='Send message', exact=True).click()
        await page.get_by_text('Working together.', exact=True).wait_for()
        submitted = next(body for path, body in writes if path == 'core/turn')
        assert submitted['mentioned_crew_ids'] == ['research', 'writing'], submitted
        assert submitted['conversation_id'] == CHAT
        await page.get_by_role('button', name='Expand Research Crew agents', exact=True).click()
        await page.get_by_role('button', name='Discovery: waiting', exact=True).click()
        await page.get_by_text('Cite actual source evidence', exact=True).wait_for()
        await field.fill('@Research Crew/Dis')
        await field.press('Enter')
        await field.fill((await field.input_value())+'explain your last run')
        await page.get_by_role('button', name='Send message', exact=True).click()
        await page.wait_for_timeout(300)
        direct = [body for path, body in writes if path == 'core/turn'][-1]
        assert direct['mentioned_agent_ids'] == ['research-discovery'], direct
        assert direct['mentioned_crew_ids'] == ['research'], direct
        assert not errors, errors
        output = ROOT / '.build/orchestration-check'
        output.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(output / 'chat.png'))
        print('PASS: keyboard multi-mentions, Crew/Agent stable IDs, expandable member graph, member inspection, correlated replies, permission switch, configuration diff; no page errors.')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
