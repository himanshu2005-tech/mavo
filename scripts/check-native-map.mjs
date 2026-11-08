// Read-only desktop smoke check; temporary WebView debugging must be disabled afterward.
import { mkdir, writeFile } from 'node:fs/promises';
const targets = await fetch("http://127.0.0.1:9222/json/list").then(r => r.json());
const target = targets.find(t => t.url === "http://localhost:1420/");
if (!target) throw Error("Mavo desktop window is not available");
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
try {
  const result = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("Desktop check timed out")), 20_000);
    socket.onmessage = ({data}) => {
      const message = JSON.parse(data);
      if (message.id !== 1) return;
      clearTimeout(timer);
      if (message.error || message.result?.exceptionDetails) reject(Error("Desktop smoke check failed"));
      else resolve(message.result.result.value);
    };
  });
  socket.send(JSON.stringify({id: 1, method: "Runtime.evaluate", params: {
    awaitPromise: true, returnByValue: true,
    expression: `(async () => {
      const {invoke} = await import('/node_modules/@tauri-apps/api/core.js');
      const {base_url} = await invoke('backend_config');
      const graph = !!document.querySelector('.graph-canvas');
      const modelNodes = document.querySelectorAll('.graph-route').length;
      const cards = [...document.querySelectorAll('.graph-agent:not(.graph-new)')];
      const minRenderedFont = Math.min(...cards.map(card => {const b = card.querySelector('b');
        return parseFloat(getComputedStyle(b).fontSize) * b.getBoundingClientRect().height / b.offsetHeight;}));
      const rects = [...document.querySelectorAll('.graph-agent:not(.graph-new),.graph-node,.graph-route,.graph-task')].map(el=>el.getBoundingClientRect());
      let overlaps = 0;
      rects.forEach((a,i)=>rects.slice(i+1).forEach(b=>{
        if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1) overlaps++;
      }));
      const health = (await fetch(base_url + '/health')).status;
      const unauthorized = (await fetch(base_url + '/api/v1/routes')).status;
      return {graph, modelNodes, agents:cards.length, minRenderedFont, overlaps, board:!!document.querySelector('.flow-board'), health, unauthorized, loopback: base_url.startsWith('http://127.0.0.1:')};
    })()`,
  }}));
  const report = await result;
  if (!report.graph || report.health !== 200 || report.unauthorized !== 401 || !report.loopback) {
    throw Error("Desktop smoke assertions failed");
  }
  console.log(JSON.stringify(report));
  const shot = new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(Error('Screenshot timed out')),10000);
    socket.onmessage=({data})=>{const value=JSON.parse(data);if(value.id!==2)return;clearTimeout(timer);resolve(value.result);};
  });
  socket.send(JSON.stringify({id:2,method:'Page.captureScreenshot',params:{format:'png'}}));
  const screenshot=await shot;
  const directory=new URL('../.build/map-only-check/',import.meta.url);await mkdir(directory,{recursive:true});
  await writeFile(new URL('actual-map.png',directory),Buffer.from(screenshot.data,'base64'));
} finally { socket.close(); }
