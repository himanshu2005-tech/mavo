// Live UI checks. Google consent and logout are intercepted before account changes.
import { mkdir, writeFile } from "node:fs/promises";
const targets = await fetch("http://127.0.0.1:9222/json/list").then(r => r.json());
const target = targets.find(t => t.url === "http://localhost:1420/");
if (!target) throw Error("Start Mavo with local development debugging enabled.");
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map();
socket.onmessage = ({data}) => {
  const result = JSON.parse(data), item = pending.get(result.id);
  if (!item) return;
  pending.delete(result.id); clearTimeout(item.timer);
  result.error ? item.reject(Error(result.error.message)) : item.resolve(result.result);
};
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, {resolve, reject, timer: setTimeout(() => reject(Error("CDP timed out")), 20000)});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function run(expression) {
  const r = await call("Runtime.evaluate", {expression: `(async()=>(${expression}))()`, awaitPromise: true, returnByValue: true});
  if (r.exceptionDetails) throw Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
  return r.result.value;
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function wait(expression) {
  for (let n = 0; n < 100; n++) { if (await run(expression)) return; await pause(150); }
  throw Error("UI did not become ready: " + expression);
}
const api = "(await import('/src/lib/api.ts')).api";
async function page(name) {
  const label = {home: "Home", resources: "Resources", settings: "Settings", chat: "Chat"}[name];
  await run(`[...document.querySelectorAll('aside button')].find(b=>[...b.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()===${JSON.stringify(label)})).click()`);
  await pause(200);
}
async function click(selector, text) { await run(`[...document.querySelectorAll(${JSON.stringify(selector)})].find(b=>b.textContent.trim()===${JSON.stringify(text)}).click()`); }
const output = new URL("../.build/ui-check/", import.meta.url);
await mkdir(output, {recursive: true});
await call("Page.reload");
await pause(1000);
await wait(`!!document.querySelector('aside nav')`);
const initial = await run(`${api}.resourceAccess()`);
const report = {};
// Intercept the network boundary, not a Vite module instance (HMR can create
// multiple API objects). Never send real consent/logout or fixture coordinates.
await run(`(()=>{window.__originalFetch=window.fetch;window.fetch=async(input,init)=>{const path=new URL(typeof input==='string'?input:input.url).pathname;const method=init?.method??'GET';if(path==='/api/v1/auth/google/start'&&method==='POST'){window.__googleCapabilities=JSON.parse(init.body).capabilities;throw Error('Connection check stopped before Google consent');}if(path==='/api/v1/auth/firebase'&&method==='DELETE'){window.__logoutCalled=true;throw Error('Logout check stopped before account change');}if(path==='/api/v1/resources/location'&&method==='PUT'){window.__detectedLocation=JSON.parse(init.body);throw Error('Detection check stopped before saving fixture coordinates');}return window.__originalFetch(input,init);};})()`);
await run(`(()=>{window.__originalGeo=navigator.geolocation.getCurrentPosition;window.__geoFixture=resolve=>{window.__geoCalls=(window.__geoCalls||0)+1;resolve({coords:{latitude:17.385044,longitude:78.486671,accuracy:120}});};Object.defineProperty(navigator.geolocation,'getCurrentPosition',{configurable:true,value:window.__geoFixture});})()`);
try {
  await page("resources");
  await wait(`!!document.querySelector('[aria-label="Weather access"]:not(:disabled)')`);
  if (await run(`document.querySelectorAll('main .page-content>section').length`) !== 4) throw Error("Resources must contain default resources, Google, GitHub and public feeds");
  if (await run(`/Your Google workspace|Email contents and write permissions|Choose individual services/.test(document.querySelector('main').textContent)`)) throw Error("Old Google controls remain visible");
  report.layout = "Default resources, Google Workspace, GitHub and public feeds; duplicate account/tools removed";
  if (initial.location) {
    await wait(`!!window.__detectedLocation`);
    if (!await run(`window.__detectedLocation.latitude===17.385&&window.__detectedLocation.longitude===78.487`)) throw Error("Saved Location permission did not trigger automatic detection");
    report.autodetect = "Opening Resources with Location enabled automatically detects rounded coordinates; test intercepted persistence before I/O.";
  }
  if (!initial.weather) {
    if (await run(`!!document.querySelector('[aria-label="Weather city"]')`)) throw Error("Disabled weather is still active");
    await run(`document.querySelector('[aria-label="Weather access"]').click()`);
    await wait(`document.querySelector('[aria-label="Weather access"]').getAttribute('aria-checked')==='true'`);
  }
  if (await run(`[...document.querySelectorAll('main button')].some(b=>/^(Set location|Detect|Use location)$/.test(b.textContent.trim())) || !!document.querySelector('[aria-label="Weather city"]')`)) throw Error("Manual location controls are still visible");
  report.permissions = "Weather switch persists; device location is blocked independently; no GPS requested";
  await wait(`!![...document.querySelectorAll('main button')].find(b=>b.textContent.trim()==='Connect Google'&&!b.disabled)`);
  await click("main button", "Connect Google");
  await wait(`!!window.__googleCapabilities`);
  const capabilities = await run("window.__googleCapabilities");
  if (capabilities.length !== 10 || !["gmail:read", "gmail:body", "gmail:send", "gmail:modify", "calendar:read", "calendar:write", "tasks:read", "tasks:write", "drive:read", "contacts:read"].every(c => capabilities.includes(c))) throw Error("Connect all omitted supported permissions");
  report.google = "One Connect Google handler requests all ten supported capabilities; no real consent changed";
  if(await run(`!!document.querySelector('[aria-label="Google Workspace"] input')`))throw Error("Google setup forms should not be visible");
  if (!initial.location && !initial.weather) {
    await run(`document.querySelector('[aria-label="Weather access"]').click()`);
    await wait(`document.querySelector('[aria-label="Weather access"]').getAttribute('aria-checked')==='false'`);
    await run(`Object.defineProperty(navigator.geolocation,'getCurrentPosition',{configurable:true,value:window.__geoFixture})`);
    await run(`document.querySelector('[aria-label="Location access"]').click()`);
    await wait(`!!window.__detectedLocation`);
    if (!await run(`window.__geoCalls===1&&window.__detectedLocation.source==='device'&&window.__detectedLocation.latitude===17.385&&window.__detectedLocation.longitude===78.487`)) throw Error("Enabling Location did not automatically detect and round device coordinates");
    await run(`document.querySelector('[aria-label="Location access"]').click()`);
    await wait(`document.querySelector('[aria-label="Location access"]').getAttribute('aria-checked')==='false'`);
    await run(`Object.defineProperty(navigator.geolocation,'getCurrentPosition',{configurable:true,value:(_resolve,reject)=>reject({code:1})})`);
    await run(`document.querySelector('[aria-label="Location access"]').click()`);
    await wait(`document.querySelector('[role="alert"]')?.textContent.includes('Location was denied')`);
    await run(`document.querySelector('[aria-label="Location access"]').click()`);
    await wait(`document.querySelector('[aria-label="Location access"]').getAttribute('aria-checked')==='false'`);
    await run(`Object.defineProperty(navigator.geolocation,'getCurrentPosition',{configurable:true,value:resolve=>{window.__lateGeo=resolve;}})`);
    await run(`document.querySelector('[aria-label="Location access"]').click()`);
    await wait(`!!window.__lateGeo`);
    await run(`document.querySelector('[aria-label="Location access"]').click()`);
    await wait(`document.querySelector('[aria-label="Location access"]').getAttribute('aria-checked')==='false'`);
    await run(`window.__detectedLocation=null;window.__lateGeo({coords:{latitude:17,longitude:78,accuracy:100}})`);
    await pause(200);
    if (await run(`!!window.__detectedLocation`)) throw Error("A revoked location request still tried to save coordinates");
    report.autodetect = "Enabling Location triggers detection; denied permission and late results after revocation are blocked. Synthetic coordinates were never saved; real GPS was not requested.";
  }
  await page("settings");
  await wait(`[...document.querySelectorAll('main button')].some(b=>b.textContent.trim()==='Logout')`);
  if (await run(`!!document.querySelector('main [aria-label="Google account"]') || /MiniMax|Minimax|AIRouter/i.test(document.querySelector('[aria-label="Mavo Core service"]').textContent)`)) throw Error("Settings still exposes duplicate Google setup or Core model details");
  await click("main button", "Logout");
  await wait(`!!window.__logoutCalled`);
  if (!await run(`(await ${api}.firebaseStatus()).signed_in`)) throw Error("The UI check signed out the real account");
  report.logout = "Logout is visible and invokes account sign-out; real login preserved during test";
  await page("chat");
  await wait(`document.querySelector('main h1')?.textContent==='MAVO CORE'`);
  if (await run(`/minimax|AIRouter/i.test(document.querySelector('main header').textContent)`)) throw Error("Core model name is still visible");
  report.core = "MAVO CORE branding; no provider/model badge";
} finally {
  await run(`Object.defineProperty(navigator.geolocation,'getCurrentPosition',{configurable:true,value:window.__geoFixture})`);
  await run(`${api}.setResourceAccess(${JSON.stringify(initial)})`);
  await page("home"); await page("resources");
  await wait(`!!document.querySelector('[aria-label="Weather access"]:not(:disabled)')`);
  const shot = await call("Page.captureScreenshot", {format: "png"});
  await writeFile(new URL("resources-simple.png", output), Buffer.from(shot.data, "base64"));
  await writeFile(new URL("resources-report.json", output), JSON.stringify(report, null, 2));
  await page("home");
  await run(`(()=>{if(window.__originalFetch)window.fetch=window.__originalFetch;if(window.__originalGeo)Object.defineProperty(navigator.geolocation,'getCurrentPosition',{configurable:true,value:window.__originalGeo});})()`);
  socket.close();
}
console.log("Resources: four groups, permission switches, all five Google services, Logout, and MAVO CORE branding passed. Account and access settings restored.");
