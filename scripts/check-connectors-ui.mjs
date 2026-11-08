// Read-only desktop connector checks. Never submits credentials, consent, or access changes.
const targets=await fetch("http://127.0.0.1:9222/json/list").then(r=>r.json());
const target=targets.find(t=>t.url==="http://localhost:1420/");
if(!target)throw Error("Launch the desktop with temporary local WebView debugging.");
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let sequence=0;const pending=new Map();
ws.onmessage=({data})=>{const r=JSON.parse(data),p=pending.get(r.id);if(p){pending.delete(r.id);clearTimeout(p.timer);r.error?p.reject(Error(r.error.message)):p.resolve(r.result);}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject,timer:setTimeout(()=>reject(Error("UI timeout")),20000)});ws.send(JSON.stringify({id,method,params}));});
async function run(expression){const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description??r.exceptionDetails.text);return r.result.value;}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(expression){for(let n=0;n<80;n++){if(await run(expression))return;await sleep(200);}throw Error("Connector UI did not become ready");}
async function page(name){await run(`[...document.querySelectorAll('aside button')].find(b=>b.textContent.trim()===${JSON.stringify(name)}).click()`);}
try{
  const providers=await run(`(async()=>{const p=await (await import('/src/lib/api.ts')).api.connectors();return p.map(v=>({provider:v.provider,connected:v.connected,configured:v.configured}));})()`);
  await page("Resources");
  if(providers.some(p=>!["google","github","feeds"].includes(p.provider)))throw Error("Unexpected account provider");
  await wait(`!!document.querySelector('[aria-label="GitHub connection"]')`);
  await wait(`!!document.querySelector('[aria-label="Google access details"]')`);
  if(await run(`!!document.querySelector('[aria-label="Google Workspace"] input')`))throw Error("Normal Google connection must not show setup fields");
  if(!await run(`document.querySelector('[aria-label="Google Workspace"]').textContent.includes('Drive')&&document.querySelector('[aria-label="Google Workspace"]').textContent.includes('Contacts')`))throw Error("Google access disclosure is missing");
  await wait(`!!document.querySelector('[aria-label="Public feeds connection"]')`);
  await run(`document.querySelector('[aria-label="Public feeds connection"] button').click()`);
  if(await run(`document.querySelector('[aria-label="Public feeds connection"]').querySelectorAll('input[type=checkbox]').length`)!==2)throw Error("Both curated feeds must be selectable");
  if(await run(`!!document.querySelector('[aria-label="Public feeds connection"] input[type=password]')`))throw Error("Public feeds must not ask for a token");
  if(await run(`!!document.querySelector('[aria-label="Microsoft 365 connection"]')`))throw Error("Removed connector is still visible");
  if(await run(`!!document.querySelector('[aria-label="GitHub connection"] input[type=password]')`))throw Error("Normal GitHub connection must not ask for a token");
  if(await run(`/Advanced token|Fine-grained token|Refresh list|Save connection/.test(document.querySelector('[aria-label="GitHub connection"]').textContent)`))throw Error("Old connection workflow is still visible");
  await page("Actions");
  await wait(`!!document.querySelector('[aria-label="Configure Run a command"]')`);
  const expected=await run(`(async()=>{const a=await (await import('/src/lib/api.ts')).api.actionPolicies();return a.actions.map(a=>a.operation);})()`);
  const actual=await run(`[...document.querySelectorAll('.action-row > button')].map(b=>b.getAttribute('aria-label'))`);
  if(actual.length!==expected.length)throw Error("Actions UI differs from the connection-aware catalog");
  if(await run(`[...document.querySelectorAll('main h2')].some(e=>/Outlook|Microsoft/.test(e.textContent))`))throw Error("Removed actions visible");
  if(!providers.find(p=>p.provider==="github").connected&&await run(`[...document.querySelectorAll('main h2')].some(e=>e.textContent==='GitHub')`))throw Error("Disconnected GitHub actions visible");
  console.log("Connector UI: compact Google/GitHub cards, access disclosures, no token/setup forms, and connected-only Actions passed. No account or permission changes.");
}finally{await page("Home");ws.close();}
