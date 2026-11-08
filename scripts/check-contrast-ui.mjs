// Read-only checks against a temporary local WebView debugging session.
import { mkdir, writeFile } from "node:fs/promises";
const targets = await fetch("http://127.0.0.1:9222/json/list").then(r=>r.json());
const target = targets.find(t=>t.url==="http://localhost:1420/");
if (!target) throw Error("Start the desktop with temporary local WebView debugging.");
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
let id=0;
const pending=new Map();
socket.onmessage=({data})=>{const m=JSON.parse(data),p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const next=++id;pending.set(next,{resolve,reject});socket.send(JSON.stringify({id:next,method,params}));});
async function run(expression){const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.text);return r.result.value;}
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const output=new URL("../.build/ui-check/",import.meta.url);
await mkdir(output,{recursive:true});
const report=[];
try {
  for(const compact of [false,true]) {
    if(compact) await call("Emulation.setDeviceMetricsOverride",{width:920,height:640,deviceScaleFactor:1,mobile:false});
    for(const page of ["Home","Chat","Crew","Goals","Resources","Actions","Activity","Settings"]) {
      await run(`[...document.querySelectorAll('aside button')].find(b=>[...b.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()===${JSON.stringify(page)})).click()`);
      await pause(450);
      if(page==="Actions") {
        await run(`document.querySelector('[aria-label="Configure Run a command"]').click()`);
        await pause(150);
        // Exercise the real UI serializer without changing the user's policy.
        await run(`(()=>{window.__contrastFetch=window.fetch;window.fetch=async(input,init)=>{if(new URL(typeof input==='string'?input:input.url).pathname==='/api/v1/action-policies'&&init?.method==='PUT'){window.__savedRule=JSON.parse(init.body).rule;throw Error('UI check stopped before changing action access.');}return window.__contrastFetch(input,init);};[...document.querySelectorAll('.action-row button')].find(b=>b.textContent.trim()==='Save').click();})()`);
        await pause(350);
        if(!await run(`window.__savedRule&&!('revision' in window.__savedRule)`))throw Error("Action save included read-only policy metadata");
        await run(`window.fetch=window.__contrastFetch`);
      }
      if(page==="Resources") {await run(`document.querySelector('[aria-label="Google access details"] summary').click()`);await pause(150);}
      if(page==="Settings") {await run(`[...document.querySelectorAll('main button')].find(b=>b.textContent.trim()==='Add connection').click()`);await pause(150);}
      if(page==="Crew") {
        await run(`[...document.querySelectorAll('main button')].find(b=>b.textContent.trim()==='Create Crew').click()`);await pause(200);
        await run(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Advanced').click()`);await pause(150);
      }
      const state=await run(`(()=>{
        const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d');
        const rgba=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].map((v,i)=>i===3?v/255:v);};
        const lum=c=>c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
        const background=el=>{const layers=[];for(let n=el;n;n=n.parentElement)layers.unshift(rgba(getComputedStyle(n).backgroundColor));let result=[9,9,9];for(const c of layers)result=result.map((v,i)=>c[i]*c[3]+v*(1-c[3]));return result;};
        const failures=[];let checked=0;
        for(const el of document.querySelectorAll('aside *,main *')) {
          if(!(el instanceof HTMLElement)||!el.getClientRects().length||el.closest('button:disabled,fieldset:disabled,[aria-hidden=true]'))continue;
          if(![...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()))continue;
          const s=getComputedStyle(el),fg=rgba(s.color),bg=background(el),a=lum(fg),b=lum(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
          const large=parseFloat(s.fontSize)>=24||(parseFloat(s.fontSize)>=18.66&&Number(s.fontWeight)>=700);
          checked++;if(ratio<(large?3:4.5))failures.push({tag:el.tagName,classes:el.className,ratio:Math.round(ratio*100)/100});
        }
        return {scheme:getComputedStyle(document.documentElement).colorScheme,checked,failures,overflow:document.documentElement.scrollWidth>innerWidth||document.querySelector('main').scrollWidth>document.querySelector('main').clientWidth};
      })()`);
      report.push({page,compact,...state});
      if(state.scheme!=="dark"||state.overflow||state.failures.length) throw Error(JSON.stringify(report.at(-1)));
      if(!compact){const shot=await call("Page.captureScreenshot",{format:"png"});await writeFile(new URL(`dark-${page.toLowerCase()}.png`,output),Buffer.from(shot.data,"base64"));}
      if(page==="Crew")await run(`document.querySelector('[aria-label="Close Crew form"]').click()`);
    }
  }
  console.log("All eight pages: black surfaces, readable text contrast, and normal/compact layouts passed.");
} finally {
  await run(`(()=>{if(window.__contrastFetch){window.fetch=window.__contrastFetch;delete window.__contrastFetch;}})()`);
  await call("Emulation.clearDeviceMetricsOverride");
  await writeFile(new URL("contrast-report.json",output),JSON.stringify(report,null,2));
  socket.close();
}
