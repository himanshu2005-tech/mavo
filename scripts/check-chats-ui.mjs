// Live native WebView smoke: only its own new empty chats are created/deleted.
// No model calls, messages, Crew, resource grants, or existing chats are changed.
const targets=await fetch("http://127.0.0.1:9222/json/list").then(r=>r.json());
const target=targets.find(t=>t.url==="http://localhost:1420/");
if(!target)throw Error("Start Mavo with temporary loopback WebView debugging.");
const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let sequence=0;
const waiting=new Map();
ws.onmessage=({data})=>{const response=JSON.parse(data),entry=waiting.get(response.id);if(entry){waiting.delete(response.id);clearTimeout(entry.timer);response.error?entry.reject(Error(response.error.message)):entry.resolve(response.result);}};
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;waiting.set(id,{resolve,reject,timer:setTimeout(()=>reject(Error("UI check timed out")),20000)});ws.send(JSON.stringify({id,method,params}));});
async function run(expression){const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description??r.exceptionDetails.text);return r.result.value;}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(expression){for(let n=0;n<100;n++){if(await run(expression))return;await pause(150);}throw Error("Chat UI did not reach expected state");}
const api="(await import('/src/lib/api.ts')).api";
const store="(await import('/src/store/app.ts')).useAppStore";
async function input(selector,value){await run(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});if(!node)throw Error('Input missing');Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node),'value').set.call(node,${JSON.stringify(value)});node.dispatchEvent(new Event('input',{bubbles:true}));})()`);}
const created=[];
let original=[],originalActive=null;
try{
  original=await run(`(async()=> (await ${api}.coreConversations()).map(c=>c.id))()`);
  originalActive=await run(`(async()=>${store}.getState().activeConversationId)()`);
  await run(`(async()=>${store}.getState().setPage('chat'))()`);
  await until(`!!document.querySelector('nav[aria-label=Chats] > button:not(:disabled)')`);
  for(let n=0;n<2;n++){
    const known=[...original,...created];
    await until(`!!document.querySelector('nav[aria-label=Chats] > button:not(:disabled)')`);
    await run(`document.querySelector('nav[aria-label=Chats] button').click()`);
    await until(`(async()=>{const id=${store}.getState().activeConversationId;return !!id&&!${JSON.stringify(known)}.includes(id);})()`);
    created.push(await run(`(async()=>${store}.getState().activeConversationId)()`));
    if(n===0){
      await input('textarea[aria-label="Message Mavo"]',"Unsent first chat draft");
      await run(`document.querySelector('.chat-history-row[data-active=true] button[aria-label^=Rename]').click()`);
      await until(`!!document.querySelector('[role=dialog] input')`);
      await input('[role=dialog] input',"Mavo UI smoke test chat");
      await run(`document.querySelector('[role=dialog]').requestSubmit()`);
      await until(`!document.querySelector('[role=dialog]')`);
    }
  }
  if(await run(`document.querySelector('textarea[aria-label="Message Mavo"]').value`)!=="")throw Error("Input leaked between chats");
  await input('textarea[aria-label="Message Mavo"]',"Unsent second chat draft");
  await run(`document.querySelector('[data-chat-id="${created[0]}"] .chat-history-select').click()`);
  await until(`document.querySelector('textarea[aria-label="Message Mavo"]').value==='Unsent first chat draft'`);
  const persisted=await run(`(async()=> (await ${api}.coreConversations()).map(c=>({id:c.id,title:c.title,messages:c.messages.length})))()`);
  if(!persisted.some(c=>c.id===created[0]&&c.title==="Mavo UI smoke test chat"&&c.messages===0))throw Error("Rename/empty history was not saved");
  for(const id of created){
    await run(`document.querySelector('[data-chat-id="${id}"] button[aria-label^=Delete]').click()`);
    await until(`!!document.querySelector('[role=dialog]')`);
    if(!await run(`document.querySelector('[role=dialog]').textContent.includes('Crew and their permissions will not be deleted')`))throw Error("Missing deletion explanation");
    await run(`[...document.querySelectorAll('[role=dialog] button')].find(b=>b.textContent.trim()==='Delete chat').click()`);
    await until(`!document.querySelector('[data-chat-id="${id}"]')&&!document.querySelector('[role=dialog]')`);
  }
  const remaining=await run(`(async()=> (await ${api}.coreConversations()).map(c=>c.id))()`);
  if(original.some(id=>!remaining.includes(id)))throw Error("Existing conversation changed");
  console.log("Chat UI: New chat, saved history, rename, selection, isolated drafts and confirmed delete passed. Existing chats preserved; no model calls.");
}finally{
  const remaining=await run(`(async()=> (await ${api}.coreConversations()).map(c=>c.id))()`);
  for(const id of created.filter(id=>remaining.includes(id)))await run(`(async()=>${api}.deleteConversation(${JSON.stringify(id)}))()`);
  await run(`(async()=>{const state=${store}.getState();state.setConversations(await ${api}.coreConversations());if(${JSON.stringify(originalActive)}&&state.conversations.some(c=>c.id===${JSON.stringify(originalActive)}))state.setActiveConversation(${JSON.stringify(originalActive)});state.setPage('home');})()`);
  ws.close();
}
