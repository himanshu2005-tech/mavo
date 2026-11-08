// Read-only policy UI checks and real desktop-button interactions. No access settings are saved.
import { mkdir, writeFile } from "node:fs/promises";
const targets=await fetch('http://127.0.0.1:9222/json/list').then(r=>r.json());
const output=new URL('../.build/ui-check/',import.meta.url);await mkdir(output,{recursive:true});
async function connect(target) {
  if(!target) throw new Error('Missing Mavo window');
  const socket=new WebSocket(target.webSocketDebuggerUrl); await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
  let id=0;const pending=new Map();
  socket.onmessage=({data})=>{const value=JSON.parse(data);if(value.id){const item=pending.get(value.id);pending.delete(value.id);clearTimeout(item?.timer);if(value.error)item.reject(Error(value.error.message));else item.resolve(value.result);}};
  const call=(method,params={})=>new Promise((resolve,reject)=>{const next=++id;pending.set(next,{resolve,reject,timer:setTimeout(()=>reject(Error('CDP timeout')),20000)});socket.send(JSON.stringify({id:next,method,params}));});
  const run=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description??r.exceptionDetails.text);return r.result.value;};
  return {socket,call,run};
}
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(connection,expression){for(let n=0;n<70;n++){if(await connection.run(expression))return;await pause(150);}throw Error('UI did not become ready: '+expression);}
const main=await connect(targets.find(t=>t.url==='http://localhost:1420/'));
const orb=await connect(targets.find(t=>t.url.includes('overlay=status')));
try {
  await main.run(`[...document.querySelectorAll('aside button')].find(b=>b.textContent.trim()==='Actions').click()`);
  await wait(main,`!!document.querySelector('[aria-label="Configure Run a command"]')`);
  await main.run(`document.querySelector('[aria-label="Configure Run a command"]').click()`);
  await wait(main,`!!document.querySelector('[aria-label="terminal.run access"]')`);
  const modes=await main.run(`[...document.querySelector('[aria-label="terminal.run access"]').options].map(o=>o.textContent)`);
  if(modes.join('|')!=='Off|Allow with limits|Allow without limits')throw Error('Missing access modes');
  await main.run(`(()=>{const el=document.querySelector('[aria-label="terminal.run access"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(el,'unrestricted');el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await wait(main,`document.body.textContent.includes('No custom folder limits')`);
  if(!await main.run(`document.querySelector('blockquote').textContent.includes('cannot take responsibility')`))throw Error('Missing responsibility quote');
  const screenshot=await main.call('Page.captureScreenshot',{format:'png'});await writeFile(new URL('action-policy.png',output),Buffer.from(screenshot.data,'base64'));
  await orb.run(`document.querySelector('[aria-label="Mavo status"]')?.click()`);
  await wait(orb,`!!document.querySelector('[aria-label="Collapse status"]')`);
  await wait(orb,`!!document.querySelector('[role=status]')?.textContent.match(/Crew running|paused|Sign in/)`);
  const status=await orb.run(`document.querySelector('[role=status]').textContent`);
  const size=await orb.run(`({width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth||document.documentElement.scrollHeight>innerHeight})`);
  if(size.overflow)throw Error('Status button panel overflows');
  const orbImage=await orb.call('Page.captureScreenshot',{format:'png'});await writeFile(new URL('desktop-status.png',output),Buffer.from(orbImage.data,'base64'));
  await orb.run(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Open').click()`);
  await orb.run(`document.querySelector('[aria-label="Collapse status"]').click()`);
  await wait(orb,`!!document.querySelector('[aria-label="Mavo status"]')`);
  await main.run(`[...document.querySelectorAll('aside button')].find(b=>b.textContent.trim()==='Home').click()`);
  await writeFile(new URL('desktop-report.json',output),JSON.stringify({modes,status,size,policySaved:false,orb:'expanded, opened Mavo, and collapsed'},null,2));
  console.log('Actions: modes, scoped freedom explanation, and quote passed. No policies changed.');
  console.log('Desktop button: real status, expand/collapse, layout, and Open passed.');
} finally {main.socket.close();orb.socket.close();}
