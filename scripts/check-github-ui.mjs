// Synthetic frontend workflow only. Blocks all GitHub changes at the fetch/IPC boundary.
const targets=await fetch('http://127.0.0.1:9222/json/list').then(r=>r.json());
const target=targets.find(t=>t.url==='http://localhost:1420/');
if(!target)throw Error('Start Mavo with temporary WebView debugging.');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.onopen=r);
let id=0;const pending=new Map();
ws.onmessage=({data})=>{const r=JSON.parse(data),p=pending.get(r.id);if(p){pending.delete(r.id);clearTimeout(p.timer);r.error?p.reject(Error(r.error.message)):p.resolve(r.result);}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject,timer:setTimeout(()=>reject(Error('UI check timed out')),20000)});ws.send(JSON.stringify({id:n,method,params}));});
async function run(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description??r.exceptionDetails.text);return r.result.value;}
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(expression){for(let n=0;n<100;n++){if(await run(expression))return;await pause(150);}throw Error('GitHub UI did not become ready');}
const page=name=>run(`[...document.querySelectorAll('aside button')].find(b=>b.textContent.trim().toLowerCase()===${JSON.stringify(name)}).click()`);
try{
  await call('Page.reload');
  await wait(`!!document.querySelector('aside nav')`);
  const phase=await run(`(async()=>{return (await (await import('/src/lib/api.ts')).api.githubAuth()).phase;})()`);
  if(['pending','exchanging','installing','select_repositories'].includes(phase))throw Error('Finish the real GitHub sign-in before running synthetic UI checks.');
  await page('home');
  await run(`(()=>{
    window.__githubFetch=window.fetch;
    window.__githubFixture={configured:true,phase:'idle',verification_uri:'https://github.com/login/device',installation_url:'https://github.com/apps/mavo-fixture/installations/new',repositories:[],coverage:'Fixture only',error:null};
    window.__githubRequests=[];
    window.__githubProvider={provider:'github',name:'GitHub',configured:true,connected:false,email:null,capabilities:[],available_capabilities:[],repositories:[]};
    window.fetch=async(input,init)=>{
      const path=new URL(typeof input==='string'?input:input.url).pathname;
      const json=v=>new Response(JSON.stringify(v),{status:200,headers:{'content-type':'application/json'}});
      if(path.startsWith('/api/v1/connectors/github/')){
        window.__githubRequests.push({path,body:init?.body?JSON.parse(init.body):null});
        if(path.endsWith('/start'))window.__githubFixture={...window.__githubFixture,phase:'pending',user_code:'TEST-CODE'};
        if(path.endsWith('/authorize')){window.__githubSaved=JSON.parse(init.body);window.__githubFixture.phase='complete';return json({provider:'github',connected:true});}
        if(path.endsWith('/revoke')){window.__githubProvider={...window.__githubProvider,connected:false,capabilities:[],repositories:[]};window.__githubFixture.phase='idle';window.__githubRevoked=true;return json({local_disconnected:true,remote_revoked:false,requires_provider_action:true,manage_url:'https://github.com/settings/apps/authorizations'});}
        return json(window.__githubFixture);
      }
      if(path==='/api/v1/connectors'){
        const response=await window.__githubFetch(input,init);const providers=await response.json();
        return json(providers.map(p=>p.provider==='github'?{...p,connected:false,auth_mode:null,capabilities:[],repositories:[]}:p));
      }
      return window.__githubFetch(input,init);
    };
  })()`);
  await run(`(async()=>{const React=(await import('/node_modules/.vite/deps/react.js')).default;const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;const {GitHubSignIn}=await import('/src/components/GitHubSignIn.tsx');const div=document.createElement('div');div.id='mavo-github-test';document.body.appendChild(div);window.__githubRoot=createRoot(div);window.__githubRender=()=>window.__githubRoot.render(React.createElement(GitHubSignIn,{provider:window.__githubProvider,onChanged:()=>window.__githubRender(),openUrl:async url=>{window.__githubOpened=url;}}));window.__githubRender();})()`);
  await wait(`!![...document.querySelectorAll('#mavo-github-test [aria-label="GitHub connection"] button')].find(b=>b.textContent==='Connect GitHub'&&!b.disabled)`);
  if(await run(`!!document.querySelector('#mavo-github-test input[type=password]')`))throw Error('Default connection exposed token form');
  await run(`document.querySelector('#mavo-github-test [aria-label="GitHub connection"] button').click()`);
  await wait(`!!document.querySelector('#mavo-github-test [aria-label="GitHub verification code"]')`);
  if(await run(`document.querySelector('#mavo-github-test [aria-label="GitHub verification code"]').textContent`)!=='TEST-CODE')throw Error('User code not shown');
  await wait(`window.__githubOpened==='https://github.com/login/device'`).catch(async()=>{throw Error(JSON.stringify(await run(`({opened:window.__githubOpened??null,error:document.querySelector('#mavo-github-test [role=alert]')?.textContent,requests:window.__githubRequests.map(r=>r.path)})`)));});
  if(!await run(`window.__githubRequests.find(r=>r.path.endsWith('/start')).body.simple===true`))throw Error('Normal connection did not request the simple flow');
  await run(`window.__githubFixture={...window.__githubFixture,phase:'installing',user_code:null}`);
  await wait(`window.__githubOpened==='https://github.com/apps/mavo-fixture/installations/new'`);
  await run(`window.__githubProvider={...window.__githubProvider,connected:true,email:'fixture',capabilities:['github:repo:read'],repositories:['fixture/one']};window.__githubFixture={...window.__githubFixture,phase:'complete'};`);
  await wait(`document.querySelector('#mavo-github-test [aria-label="GitHub connection status"]').textContent==='Connected'`);
  if(await run(`!!document.querySelector('#mavo-github-test input')||/Save connection|Refresh list|Advanced token/.test(document.querySelector('#mavo-github-test').textContent)`))throw Error('Normal connection still has extra setup steps');
  await run(`[...document.querySelectorAll('#mavo-github-test button')].find(b=>b.textContent==='Revoke access').click()`);
  await wait(`window.__githubRevoked&&document.querySelector('#mavo-github-test [aria-label="GitHub connection status"]').textContent==='Not connected'`);
  await wait(`window.__githubOpened==='https://github.com/settings/apps/authorizations'`);
  console.log('GitHub UI: one Connect, automatic installation handoff/completion, Connected badge and Revoke access passed. No keys, permission forms, Save or Refresh steps. Synthetic checks only; real accounts unchanged.');
}finally{
  await page('home');
  await run(`(()=>{window.__githubRoot?.unmount();document.getElementById('mavo-github-test')?.remove();if(window.__githubFetch){window.fetch=window.__githubFetch;for(const key of ['__githubFetch','__githubRoot','__githubFixture','__githubRequests','__githubOpened','__githubSaved','__githubRender','__githubProvider','__githubRevoked'])delete window[key];}})()`);
  ws.close();
}
