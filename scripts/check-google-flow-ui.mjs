// Native API boundary inspection; --open starts consent, never approves it.
// Prints only safe configuration/status fields, never URLs, tokens or account IDs.
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let target;
for(let attempt=0;attempt<80;attempt++){
  try{const targets=await fetch("http://127.0.0.1:9222/json/list").then(r=>r.json());target=targets.find(t=>t.url==="http://localhost:1420/");if(target)break;}catch{}
  await pause(250);
}
if(!target)throw Error("Start Mavo with temporary local WebView debugging.");
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let id=0;const waiting=new Map();
ws.onmessage=({data})=>{const response=JSON.parse(data),entry=waiting.get(response.id);if(entry){waiting.delete(response.id);clearTimeout(entry.timer);response.error?entry.reject(Error(response.error.message)):entry.resolve(response.result);}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const next=++id;waiting.set(next,{resolve,reject,timer:setTimeout(()=>reject(Error("Native check timed out")),20000)});ws.send(JSON.stringify({id:next,method,params}));});
async function run(expression){const result=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(result.exceptionDetails.exception?.description??result.exceptionDetails.text);return result.result.value;}
try{
  const status=await run(`(async()=>{const api=(await import('/src/lib/api.ts')).api;const [identity,google,connectors]=await Promise.all([api.firebaseStatus(),api.googleStatus(),api.connectors()]);const github=connectors.find(p=>p.provider==='github');return {workspace_signed_in:identity.signed_in,google_configured:google.configured,desktop_has_client_secret:google.has_client_secret,google_signed_in:google.signed_in,github_connected:github.connected,google_revocation_confirmed:google.revocation?.remote_revoked??false,github_revocation_user_confirmed:!!github.revocation&&!github.revocation.requires_provider_action,capabilities:google.capabilities,phase:google.phase,error_code:google.error_code};})()`);
  console.log(JSON.stringify(status));
  if(process.argv.includes("--check-metadata")){
    const metadata=await run(`(async()=>{const api=(await import('/src/lib/api.ts')).api;const results=[];for(const resource of ['drive','contacts']){const snapshot=await api.googleResource(resource);const allowed=resource==='drive'?['id','name','mimeType','modifiedTime','webViewLink']:['id','name','emails','phones'];if(snapshot.items.length>100||snapshot.items.some(item=>Object.keys(item).some(key=>!allowed.includes(key))))throw Error('Unexpected Google snapshot shape');results.push({resource,items:snapshot.items.length,metadata_only:true});}return results;})()`);
    console.log(JSON.stringify({bounded_metadata:metadata}));
  }
  if(process.argv.includes("--check-services")){
    const result=await run(`(async()=>{const check=await (await import('/src/lib/api.ts')).api.checkGoogleConnection();return {services:check.services.map(s=>({resource:s.resource,status:s.status}))};})()`);
    console.log(JSON.stringify(result));
    if(result.services.some(service=>service.status!=="connected"))throw Error("A Google service has not verified its connected read access.");
  }
  if(process.argv.includes("--open")){
    if(!status.workspace_signed_in||!status.google_configured)throw Error("Mavo login and publisher Google identity must be configured first.");
    await run(`(async()=>{const store=(await import('/src/store/app.ts')).useAppStore;store.getState().setPage('resources');const api=(await import('/src/lib/api.ts')).api;const result=await ${process.argv.includes("--extensions")?"api.connectGoogleCapabilities(['drive:read','contacts:read'])":"api.connectAllGoogle()"};await (await import('/node_modules/@tauri-apps/api/core.js')).invoke('open_google_url',{url:result.authorization_url});})()`);
    console.log("Google consent opened. Complete it yourself; no approval has been automated.");
  }
}finally{ws.close();}
