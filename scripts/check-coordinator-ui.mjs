// Live natural-language builder check. Uses a separate planning session and never grants inbox access.
import { mkdir, writeFile } from "node:fs/promises";
const targets = await fetch("http://127.0.0.1:9222/json/list").then(r => r.json());
const target = targets.find(t => t.url === "http://localhost:1420/");
if (!target) throw Error("Start the desktop with local debugging enabled first.");
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let id = 0;
const pending = new Map();
socket.onmessage = ({data}) => {
  const value = JSON.parse(data);
  if (!value.id) return;
  const item = pending.get(value.id);
  if (!item) return;
  pending.delete(value.id); clearTimeout(item.timer);
  value.error ? item.reject(Error(value.error.message)) : item.resolve(value.result);
};
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const next = ++id;
    pending.set(next, {resolve, reject, timer: setTimeout(() => reject(Error("CDP timed out")), 225000)});
    socket.send(JSON.stringify({id: next, method, params}));
  });
}
async function run(expression) {
  const result = await call("Runtime.evaluate", {expression: `(async()=>(${expression}))()`, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function wait(expression, timeout = 15000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) { if (await run(expression)) return; await pause(150); }
  throw Error("UI timed out: " + expression);
}
const api = "(await import('/src/lib/api.ts')).api";
function click(selector, text) { return run(`[...document.querySelectorAll(${JSON.stringify(selector)})].find(b=>b.textContent.trim()===${JSON.stringify(text)}||[...b.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()===${JSON.stringify(text)})).click()`); }
async function fill(text) {
  await run(`(()=>{const field=document.querySelector('[aria-label="Crew responsibility"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(field,${JSON.stringify(text)});field.dispatchEvent(new Event('input',{bubbles:true}));})()`);
}
let created;
const report = {};
const output = new URL("../.build/ui-check/", import.meta.url);
await mkdir(output, {recursive: true});
try {
  await run(`document.querySelector('[aria-label="Close builder"]')?.click()`);
  const initial = await run(`(await ${api}.crew()).map(c=>c.id)`);
  const initialDrafts = await run(`(await ${api}.crewDrafts()).map(c=>c.id)`);
  await click("aside button", "Crew");
  await click("main button", "Create Crew");
  await wait(`!!document.querySelector('[aria-label="Crew responsibility"]')`);
  await fill("Create a Communication Crew named UI planning verification. I need important college email alerts, without spam or promotions.");
  await wait(`[...document.querySelectorAll('.sheet button')].some(b=>b.textContent.trim()==='Create'&&!b.disabled)`);
  await click(".sheet button", "Create");
  await wait(`!!document.querySelector('.sheet .animate-spin')`);
  report.loading = "Animated progress displayed during live planning";
  await wait(`!!document.querySelector('.sheet [aria-label="Crew setup"]') || !!document.querySelector('.sheet [role="alert"]')`, 220000);
  const firstError = await run(`document.querySelector('.sheet [role="alert"]')?.textContent`);
  if (firstError) throw Error(firstError);
  const extra = await run(`(await ${api}.crewDrafts()).filter(c=>!${JSON.stringify(initialDrafts)}.includes(c.id))`);
  if (extra.length !== 1) throw Error("Expected exactly one saved setup draft");
  if (await run(`(await ${api}.crew()).some(c=>!${JSON.stringify(initial)}.includes(c.id))`)) throw Error("Incomplete setup leaked into Crew");
  created = extra[0].id;
  if (extra[0].is_running || extra[0].permissions.join() !== "model:invoke") throw Error("Crew granted itself access");
  report.created = "One draft saved; it is not counted or published as Crew";
  await fill("Use exactly college.edu senders, explicitly dated exams and assignment deadlines within 48 hours of computer local time. Ignore all promotional mail, newsletters, and messages without a deadline. Read only and show a short desktop notification for each matching message. Do not send, mark read, archive, delete, or modify anything. Use those defaults for any other detail. Finish the setup.");
  await wait(`[...document.querySelectorAll('.sheet button')].some(b=>b.textContent.trim()==='Send'&&!b.disabled)`);
  await click(".sheet button", "Send");
  await wait(`!document.querySelector('.sheet .animate-spin')`, 220000);
  const error = await run(`document.querySelector('.sheet [role="alert"]')?.textContent`);
  if (error) throw Error(error);
  const refined = await run(`(await ${api}.crewDrafts()).filter(c=>!${JSON.stringify(initialDrafts)}.includes(c.id))`);
  if (refined.length !== 1 || refined[0].id !== created) throw Error("Follow-up created a duplicate Crew");
  if (refined[0].configuration_plan.draft.trigger_type !== "gmail_message") throw Error("Email was configured as a timer");
  report.refinement = "Follow-up updated the same Crew with a mail event trigger";
  const shot = await call("Page.captureScreenshot", {format: "png"});
  await writeFile(new URL("automatic-crew-setup.png", output), Buffer.from(shot.data, "base64"));
  await click(".sheet button", "Done");
  if (await run(`!!document.querySelector('.crew-detail [aria-label="Crew setup"]')`)) throw Error("Unverified draft was added to Crew");
  await click("aside button", "Chat");
  await wait(`document.querySelector('main h1')?.textContent==='MAVO CORE'`);
  if (await run(`!!document.querySelector('main header select') || !document.querySelector('nav[aria-label=Chats]')`)) throw Error("Missing chat history or an unexpected provider picker");
  report.chat = "Independent Core chats, publisher-funded coordinator, no agent-model picker";
  await click("aside button", "Settings");
  if (!await run(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='Logout')`)) throw Error("Missing Logout control");
  report.login = "Mandatory signed-in workspace with Logout";
  console.log("Live builder: creation, refinement, mail trigger, loading state, and access boundary passed.");
  console.log("Chat and Settings: MAVO CORE, chat history, no route picker, and Logout passed.");
} finally {
  if (created) {
    await run(`${api}.stopCrew(${JSON.stringify(created)})`);
    await run(`${api}.deleteCrew(${JSON.stringify(created)})`);
    await run(`(async()=>{const state=(await import('/src/store/app.ts')).useAppStore.getState();state.setCrew(await ${api}.crew());state.setSetupDrafts(await ${api}.crewDrafts());})()`);
  }
  await writeFile(new URL("coordinator-report.json", output), JSON.stringify(report, null, 2));
  socket.close();
}
