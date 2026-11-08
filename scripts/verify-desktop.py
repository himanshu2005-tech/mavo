"""Exercise real desktop tools in an isolated workspace; never sends mail or touches personal tabs."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
import psutil
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from mavo.config import Settings
from mavo.credentials import DpapiCredentialStore, MemoryCredentialStore
from mavo.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def main():
    report = {}
    with TemporaryDirectory(prefix="desktop-verification-", dir=ROOT / ".build") as directory:
        folder = Path(directory)
        settings = Settings(require_login=False, data_dir=folder, automation_dir=folder / "automation", api_token=secrets.token_urlsafe(32), workers_enabled=False)
        with TestClient(create_app(settings, credentials=MemoryCredentialStore())) as client:
            client.headers["Authorization"] = "Bearer " + settings.api_token
            def policy(operation, **scope):
                response = client.put("/api/v1/action-policies", json={"operation":operation,"rule":{"enabled":True,"mode":"unrestricted",**scope}})
                response.raise_for_status()
            def action(operation, **arguments):
                response = client.post("/api/v1/actions", json={"operation":operation,"arguments":arguments})
                response.raise_for_status()
                value = response.json()
                assert value["status"] == "completed", value
                return value["result"]
            policy("terminal.run", folders=[str(folder)])
            terminal = action("terminal.run", command="node.version", folder=str(folder))
            assert terminal["exit_code"] == 0 and terminal["stdout"].startswith("v")
            report["terminal"] = "Installed Node executed without a shell in the approved folder"
            print("Terminal: PASS", flush=True)

            domains = ["example.com"]
            for operation in ("browser.open","browser.read","browser.fill","browser.click","browser.close"):
                policy(operation, domains=domains)
            opened = action("browser.open", url="https://example.com")
            assert "Example Domain" in opened["title"]
            assert action("browser.read")["elements"]
            browser = client.app.state.crew_runtime.actions.desktop
            async def add_fixture():
                await browser.page.evaluate("""() => {
                  const field=document.createElement('input'); field.id='mavo-test';
                  const button=document.createElement('button'); button.id='mavo-click';
                  button.textContent='Mavo test'; button.onclick=()=>field.dataset.clicked='yes';
                  document.body.append(field,button);
                }""")
            client.portal.call(add_fixture)
            action("browser.fill", selector="#mavo-test", text="Verified in dedicated Edge")
            action("browser.click", selector="#mavo-click")
            async def check_fixture():
                return await browser.page.locator("#mavo-test").evaluate("el=>({value:el.value,clicked:el.dataset.clicked})")
            observed = client.portal.call(check_fixture)
            assert observed == {"value":"Verified in dedicated Edge","clicked":"yes"}
            action("browser.close")
            report["browser"] = "Real public page read; actual Edge fill/click verified with inserted test controls; dedicated session closed"
            print("Browser: PASS", flush=True)
            if "--browser-only" in sys.argv:
                import hashlib
                assert (folder/"automation"/"browser"/hashlib.sha256(b"core").hexdigest()[:24]/"Default").is_dir()
                print("Per-assistant browser profile: PASS", flush=True)
                return

            output = folder / "window-text.txt"
            fixture = folder / "window_fixture.py"
            fixture.write_text("""import pathlib,sys,tkinter as tk
root=tk.Tk();root.title('Mavo desktop tool verification');root.geometry('380x160')
text=tk.Text(root);text.pack(fill='both',expand=True);text.focus_force();root.after(100,lambda:(root.lift(),text.focus_force()))
def save():
    pathlib.Path(sys.argv[1]).write_text(text.get('1.0','end-1c'),encoding='utf-8')
    root.after(100,save)
root.after(100,save);root.mainloop()
""", encoding="utf-8")
            window = subprocess.Popen([sys.executable,str(fixture),str(output)], creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                app_name = Path(sys.executable).name.lower()
                for operation in ("windows.list","windows.focus","windows.type","windows.key","windows.minimize"):
                    policy(operation, apps=[app_name])
                target = None
                for _ in range(60):
                    windows = action("windows.list")["windows"]
                    family = {window.pid} | {child.pid for child in psutil.Process(window.pid).children(recursive=True)}
                    target = next((w for w in windows if w["process_id"] in family and w["title"]=="Mavo desktop tool verification"),None)
                    if target:
                        break
                    time.sleep(.2)
                assert target, "Own test window was not found"
                target = {k:target[k] for k in ("window_id","process_id","process_started")}
                action("windows.focus", **target)
                action("windows.type", text="Mavo Windows input verified", **target)
                for _ in range(30):
                    if output.exists() and output.read_text(encoding="utf-8")=="Mavo Windows input verified":
                        break
                    time.sleep(.1)
                assert output.read_text(encoding="utf-8")=="Mavo Windows input verified"
                action("windows.key", key="ctrl+a", **target)
                action("windows.type", text="Replacement verified", **target)
                time.sleep(.3)
                assert output.read_text(encoding="utf-8")=="Replacement verified"
                action("windows.minimize", **target)
                report["windows"] = "Owned test window enumerated, focused, typed, selected/replaced, and minimized; actual text verified"
                print("Windows: PASS",flush=True)
            finally:
                for child in psutil.Process(window.pid).children(recursive=True):
                    child.terminate()
                window.terminate();window.wait(timeout=5)

            data = Path(os.environ["LOCALAPPDATA"]) / "dev.mavo.desktop"
            with sqlite3.connect((data / "mavo.db").as_uri()+"?mode=ro",uri=True) as db:
                model, credential = db.execute("SELECT model,credential_id FROM model_routes WHERE provider='airouter' ORDER BY created_at LIMIT 1").fetchone()
            key = DpapiCredentialStore(data).get(credential)
            route = client.post("/api/v1/routes",json={"name":"Desktop verification","provider":"airouter","model":model,"api_key":key}).json()
            crew = client.post("/api/v1/crew",json={"name":"Tool selection", "template_id":"coding", "route_id":route["id"], "responsibilities":f"Use terminal.run with command node.version and folder {folder}. Return an action and wait; do not invent the result.","trigger_type":"manual","daily_call_limit":1}).json()
            client.put("/api/v1/action-policies",json={"operation":"terminal.run","crew_id":crew["id"],"rule":{"enabled":True,"mode":"unrestricted"}}).raise_for_status()
            client.post(f"/api/v1/crew/{crew['id']}/start").raise_for_status()
            client.portal.call(client.app.state.crew_runtime.work,crew['id'])
            requests=[a for a in client.get('/api/v1/actions').json() if a['crew_id']==crew['id']]
            assert len(requests)==1 and requests[0]['status']=='completed', requests
            assert requests[0]['result']['stdout'].startswith('v')
            report['model']='AIRouter agent chose and executed the enabled terminal tool using its own connection'
            print('AIRouter agent tool selection: PASS',flush=True)
    (ROOT / ".build" / "desktop-verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")


if __name__=="__main__":
    main()
