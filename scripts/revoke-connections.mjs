// Explicit user-requested revocation only. Never prints tokens or account identifiers.
if(!process.argv.includes('--revoke')&&!process.argv.includes('--confirm-github'))throw Error('Use --revoke only for requested revocation, or --confirm-github after the user confirms revoking on GitHub.');
let target;
for(let n=0;n<100;n++){
  try{target=(await fetch('http://127.0.0.1:9222/json/list').then(r=>r.json())).find(t=>t.url==='http://localhost:1420/');if(target)break;}catch{}
  await new Promise(r=>setTimeout(r,250));
}
if(!target)throw Error('Start Mavo with temporary local WebView debugging.');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.onopen=r);
let id=0;const pending=new Map();
ws.onmessage=({data})=>{const r=JSON.parse(data),p=pending.get(r.id);if(p){pending.delete(r.id);clearTimeout(p.timer);r.error?p.reject(Error(r.error.message)):p.resolve(r.result);}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject,timer:setTimeout(()=>reject(Error('Revocation check timed out')),45000)});ws.send(JSON.stringify({id:n,method,params}));});
async function run(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description??r.exceptionDetails.text);return r.result.value;}
try{
  const result=await run(`(async()=>{const api=(await import('/src/lib/api.ts')).api;const receipts=[];${process.argv.includes('--confirm-github')?'receipts.push(await api.confirmGitHubRevoked());':'receipts.push(await api.revokeGoogle());receipts.push(await api.revokeGitHub());'}const google=await api.googleStatus();const github=(await api.connectors()).find(p=>p.provider==='github');return {receipts,google_connected:google.signed_in,github_connected:github.connected};})()`);
  console.log(JSON.stringify(result));
  if(result.google_connected||result.github_connected)throw Error('A Mavo connection is still active.');
  for(const receipt of result.receipts.filter(r=>r.requires_provider_action)){
    await run(`(async()=>{const invoke=(await import('/node_modules/@tauri-apps/api/core.js')).invoke;await invoke(${JSON.stringify(receipt.provider==='github'?'open_github_url':'open_google_url')},{url:${JSON.stringify(receipt.manage_url)}});})()`);
  }
  await call('Page.reload');
}finally{ws.close();}
