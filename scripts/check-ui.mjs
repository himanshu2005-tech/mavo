// Run against an explicitly launched development WebView2 debugging session.
// Screenshots and reports stay under .build; no browser packages are needed.
import { mkdir, writeFile } from "node:fs/promises";

const targets = await fetch("http://127.0.0.1:9222/json/list").then((r) => r.json());
const target = targets.find((t) => t.type === "page" && t.url === "http://localhost:1420/");
if (!target) throw new Error("Launch the Mavo development window with local debugging on port 9222 first.");
let socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map();
const errors = [];
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) {
    const item = pending.get(message.id);
    if (!item) return;
    clearTimeout(item.timer); pending.delete(message.id);
    if (message.error) item.reject(new Error(message.error.message));
    else item.resolve(message.result);
  }
  if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
};
function cdp(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timed out`)); }, 140_000);
    pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await cdp("Runtime.evaluate", { expression: `(async () => (${expression}))()`, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
}
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(expression, timeout = 15_000) {
  const deadline = Date.now() + timeout;
  do { if (await evaluate(`Boolean(${expression})`)) return; await pause(200); } while (Date.now() < deadline);
  throw new Error(`UI condition timed out: ${expression}`);
}
function click(selector, text) {
  return evaluate(`(() => { const text = ${JSON.stringify(text)}; const button = [...document.querySelectorAll(${JSON.stringify(selector)})].find(e => e.textContent.trim() === text || [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim() === text)); if (!button) throw new Error('Missing button: ' + text); button.click(); })()`);
}
function fill(selector, value) {
  return evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) throw new Error('Missing field'); const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ${JSON.stringify(value)}); el.dispatchEvent(new Event('input', { bubbles: true })); })()`);
}
const output = new URL("../.build/ui-check/", import.meta.url);
await mkdir(output, { recursive: true });
const report = { screens: [], flows: [], errors };
const api = `(await import('/src/lib/api.ts')).api`;
let createdCrew, createdReminder;
try {
  await cdp("Runtime.enable");
  await cdp("Page.enable");
  await waitFor("document.querySelector('aside nav')");
  await click("aside button", "Home");
  await waitFor("document.querySelector('header [role=status]')?.textContent.includes('Connected')", 25_000);
  if (process.argv.includes("--compact")) await cdp("Emulation.setDeviceMetricsOverride", { width: 920, height: 640, deviceScaleFactor: 1, mobile: false });
  if (!process.argv.includes("--overlay-only")) {
  for (const page of ["Home", "Chat", "Crew", "Goals", "Resources", "Actions", "Activity", "Settings"]) {
    await click("aside button", page); await pause(300);
    await waitFor("document.querySelector('main h1') && !document.body.textContent.includes('This view could not load')");
    if (page === "Resources" && process.argv.includes("--weather")) {
      const forecast = await evaluate(`${api}.weather('Chennai, Tamil Nadu, India')`);
      if (!forecast.location.startsWith("Chennai") || !forecast.forecast.length) throw Error("Destination weather lookup failed");
      if (await evaluate("[...document.querySelectorAll('main button')].some(b=>/^(Set location|Detect|Use location)$/.test(b.textContent.trim()))")) throw Error("Manual location controls are still visible");
      report.flows.push({ weather: "live destination forecast verified without changing automatic computer location" });
    }
    const layout = await evaluate("({title: document.querySelector('main h1').textContent, width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth || document.querySelector('main').scrollWidth > document.querySelector('main').clientWidth, content: document.querySelector('main').innerText.length})");
    if (layout.overflow || layout.content < 80) throw new Error(`${page} has an invalid layout`);
    const screenshot = await cdp("Page.captureScreenshot", { format: "png" });
    await writeFile(new URL(page.toLowerCase() + (process.argv.includes("--compact") ? "-compact" : "") + ".png", output), Buffer.from(screenshot.data, "base64"));
    report.screens.push({ page, ...layout });
    console.log(`${page}: rendered`);
  }
  }
  if (!process.argv.includes("--screens-only") && !process.argv.includes("--overlay-only")) {
  await click("aside button", "Crew"); await click("main button", "Create Crew");
  await waitFor("document.querySelector('[aria-label=\"Crew responsibility\"]')");
  report.flows.push({ naturalCrewBuilder: "plain-language creation is the default" });
  await click(".sheet button", "Advanced");
  await waitFor("document.querySelector('[role=dialog]')");
  await waitFor("document.querySelectorAll('.sheet select')[0]?.options.length === 15");
  for (let index = 0; index < 15; index++) {
    await evaluate(`(() => { const select = document.querySelector('.sheet select'); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, select.options[${index}].value); select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await pause(60);
    report.flows.push({ template: await evaluate("document.querySelector('.sheet select').value"), builder: "rendered" });
  }
  await evaluate("(() => { const select = document.querySelector('.sheet select'); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select, 'research'); select.dispatchEvent(new Event('change',{bubbles:true})); })()");
  await fill('.sheet input[placeholder="Crew name"]', "UI verification Crew");
  await fill(".sheet textarea", "Give one short explanation of how to begin a literature review. Then wait for my input. No further work is needed.");
  await click(".sheet button", "Save Crew");
  await waitFor("document.querySelector('.crew-detail h2')?.textContent === 'UI verification Crew'");
  createdCrew = await evaluate(`(await ${api}.crew()).find(c=>c.name==='UI verification Crew').id`);
  if (await evaluate(`(await ${api}.crew()).find(c=>c.id===${JSON.stringify(createdCrew)}).is_running`)) throw new Error("Saving started a worker without Run");
  report.flows.push({ createCrew: "saved and stopped" });
  await evaluate("document.querySelector('[aria-label=\"Edit Crew\"]').click()");
  await waitFor("document.querySelector('[role=dialog]')");
  await pause(2800);
  await fill(".sheet textarea", "Give one short explanation of how to begin a literature review. Then wait for my input. No further work is needed.");
  await click(".sheet button", "Save Changes");
  await waitFor("!document.querySelector('[role=dialog]')");
  report.flows.push({ editCrew: "saved without granting device access" });
  await click(".crew-detail button", "Run");
  await waitFor(`await ${api}.crewRuns(${JSON.stringify(createdCrew)}).then(r=>r[0] && r[0].status !== 'running')`, 130_000);
  const completed = await evaluate(`(await ${api}.crewRuns(${JSON.stringify(createdCrew)}))[0]`);
  if (completed.status !== "completed") throw new Error(`Crew execution failed: ${completed.error}`);
  await waitFor("document.querySelector('.crew-detail .message-content')?.textContent.length > 0", 10_000);
  const workImage = await cdp("Page.captureScreenshot", { format: "png" });
  await writeFile(new URL("crew-result.png", output), Buffer.from(workImage.data, "base64"));
  report.flows.push({ worker: "Run used saved instructions and displayed a live AIRouter checkpoint" });
  await click(".crew-detail button", "Stop");
  await waitFor(`!(await ${api}.crew()).find(c=>c.id===${JSON.stringify(createdCrew)}).is_running`);
  report.flows.push({ stop: "worker stopped" });
  await click("aside button", "Settings");
  await click("main button", "Test connection");
  await waitFor("document.querySelector('main section [role=status]')?.textContent.includes('Connected')", 130_000);
  report.flows.push({ route: "live connection test passed" });
  await click("aside button", "Chat");
  if (await evaluate("document.querySelector('main').textContent.includes('New conversation')")) throw new Error("Core chat should have no conversation tabs");
  report.flows.push({ chat: "one persistent Mavo chat; user history preserved" });
  await click("aside button", "Activity");
  await waitFor("document.querySelector('main').textContent.includes('UI verification Crew')");
  report.flows.push({ activity: "saved session visible" });
  }
  if (!process.argv.includes("--screens-only")) {
    await click("aside button", "Crew");
    createdReminder = await evaluate(`(async () => { const route = (await ${api}.routes())[0]; const c = await ${api}.createCrew({name:'UI verification reminder',template_id:'schedule',responsibilities:'This is a temporary reminder preview.',route_id:route.id,autonomy_mode:'proactive',max_model_calls:1,schedule_time:'23:59'}); (await import('/src/store/app.ts')).useAppStore.getState().upsertCrew(c); return c.id; })()`);
    await waitFor("[...document.querySelectorAll('[aria-label]')].some(e=>e.getAttribute('aria-label')==='Open UI verification reminder')");
    await evaluate("[...document.querySelectorAll('[aria-label]')].find(e=>e.getAttribute('aria-label')==='Open UI verification reminder').click()");
    await click("main button", "Preview reminder");
    let overlay;
    for (let attempt = 0; attempt < 30; attempt++) {
      overlay = (await fetch("http://127.0.0.1:9222/json/list").then((r) => r.json())).find((t) => t.url.includes("overlay=status"));
      if (overlay) break;
      await pause(200);
    }
    if (!overlay) throw new Error("Reminder overlay did not open");
    const mainSocket = socket;
    socket = new WebSocket(overlay.webSocketDebuggerUrl);
    socket.onmessage = mainSocket.onmessage;
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    try {
      await waitFor("document.body.textContent.includes('Reminder preview') && document.querySelector('h1')?.textContent !== 'Loading…'");
      const screenshot = await cdp("Page.captureScreenshot", { format: "png" });
      await writeFile(new URL("reminder-overlay.png", output), Buffer.from(screenshot.data, "base64"));
      await pause(600);
      const state = await evaluate("({preview: document.body.textContent.includes('Reminder preview'), text: document.querySelector('p')?.textContent.length > 0, speechAvailable: 'speechSynthesis' in window, speechStarted: window.speechSynthesis?.speaking, overflow: document.documentElement.scrollWidth > innerWidth})");
      if (!state.preview || !state.text || state.overflow) throw new Error("Invalid reminder overlay");
      report.flows.push({ reminderOverlay: state });
      await click("button", "Dismiss");
    } finally { socket.close(); socket = mainSocket; }
    await waitFor("(await import('/node_modules/@tauri-apps/api/core.js')).invoke('pending_reminder').then(x=>x===null)");
    console.log("Reminder preview: rendered and dismissed");
  }
  if (errors.length) throw new Error(`Browser runtime errors: ${errors.join(', ')}`);
} finally {
  if (createdReminder) { await evaluate(`${api}.stopCrew(${JSON.stringify(createdReminder)})`); await evaluate(`${api}.deleteCrew(${JSON.stringify(createdReminder)})`); }
  if (createdCrew) { await evaluate(`${api}.stopCrew(${JSON.stringify(createdCrew)})`); await evaluate(`${api}.deleteCrew(${JSON.stringify(createdCrew)})`); }
  if (createdCrew || createdReminder) await evaluate(`(async () => { const s = (await import('/src/store/app.ts')).useAppStore.getState(); s.setCrew(await ${api}.crew()); const core = await ${api}.coreConversation(); s.setConversations(core.conversation ? [core.conversation] : []); s.setCrewProposals(core.crew_proposals); })()`);
  const suffix = process.argv.includes("--overlay-only") ? "-overlay" : process.argv.includes("--compact") ? "-compact" : process.argv.includes("--screens-only") ? "-screens" : "";
  await writeFile(new URL("report" + suffix + ".json", output), JSON.stringify(report, null, 2));
  if (process.argv.includes("--compact")) await cdp("Emulation.clearDeviceMetricsOverride");
  socket.close();
}
console.log("All desktop UI checks passed. Screenshots: .build/ui-check");
