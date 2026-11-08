// Exercise the user's requested read-only C-drive check, without changing its configuration.
import { mkdir, writeFile } from "node:fs/promises";
const targets=await fetch("http://127.0.0.1:9222/json/list").then(r=>r.json());
function connect(target){
  if(!target)throw Error("Mavo window unavailable");
  const socket=new WebSocket(target.webSocketDebuggerUrl);let sequence=0;const pending=new Map();
  socket.onmessage=({data})=>{const value=JSON.parse(data);const p=pending.get(value.id);if(p){pending.delete(value.id);clearTimeout(p.timer);value.error?p.reject(Error(value.error.message)):p.resolve(value.result);}};
  const ready=new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
  const call=async(method,params={})=>{await ready;return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject,timer:setTimeout(()=>reject(Error("UI check timed out")),20000)});socket.send(JSON.stringify({id,method,params}));});};
  const run=async expression=>{const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description??r.exceptionDetails.text);return r.result.value;};
  return{socket,call,run};
}
const main=connect(targets.find(t=>t.url==="http://localhost:1420/"));
const status=connect(targets.find(t=>t.url.includes("overlay=status")));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(expression){for(let n=0;n<150;n++){if(await status.run(expression))return;await sleep(250);}throw Error("Desktop update did not arrive");}
try{
  const crew=await main.run(`(async()=>{const api=(await import('/src/lib/api.ts')).api;const c=(await api.crew()).find(c=>c.template_id==='computer'&&/C.*Drive|Drive.*C/i.test(c.name));if(!c)throw Error('No saved C-drive Crew');if(!c.is_running)throw Error('Crew is stopped; not starting without direction');await api.checkCrew(c.id);return{id:c.id,name:c.name};})()`);
  await wait(`document.querySelector('[aria-label="Desktop Crew update"] h1')?.textContent===${JSON.stringify(crew.name)}&&document.querySelector('[aria-label="Desktop Crew update"]')?.textContent.includes('GB free')`);
  if(!await status.run(`document.body.textContent.includes('% free')&&!document.querySelector('[aria-label="Desktop Crew update"]').textContent.includes('Delete files')`))throw Error("Missing actual free space");
  if(await status.run(`document.documentElement.scrollWidth>innerWidth||document.documentElement.scrollHeight>innerHeight`))throw Error("Notification panel overflows");
  await mkdir(new URL("../.build/ui-check/",import.meta.url),{recursive:true});
  const shot=await status.call("Page.captureScreenshot",{format:"png"});
  await writeFile(new URL("../.build/ui-check/desktop-drive-update.png",import.meta.url),Buffer.from(shot.data,"base64"));
  await status.run(`document.querySelector('[aria-label="Collapse status"]').click()`);
  await wait(`!!document.querySelector('[aria-label="Mavo status · unread update"]')`);
  await status.run(`document.querySelector('[aria-label="Mavo status · unread update"]').click()`);
  await wait(`!!document.querySelector('[aria-label="Desktop Crew update"]')`);
  console.log("Actual C-drive Crew: read-only Check now, auto-expanded attached update, free GB/percent, readable layout, collapse/unread/reopen passed. Notification left visible; Crew settings unchanged.");
}finally{await main.run(`(async()=>{await (await import('/node_modules/@tauri-apps/api/core.js')).invoke('show_main',{activity:false});})()`);main.socket.close();status.socket.close();}
