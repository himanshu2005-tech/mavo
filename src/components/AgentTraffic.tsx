import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { handoffStatus, type AgentMessage } from '../lib/orchestration';
import { localTimestamp } from '../lib/time';
import { useAppStore } from '../store/app';

export function useAgentMessages(crewId?:string,enabled=true){
  const [messages,setMessages]=useState<AgentMessage[]>([]);
  const epoch=useAppStore(s=>s.workspaceEpoch);
  useEffect(()=>{
    if(!enabled){setMessages([]);return;}
    let live=true;setMessages([]);
    const poll=async()=>{if(document.hidden)return;try{const rows=await api.agentMessages(crewId);if(live&&useAppStore.getState().workspaceEpoch===epoch)setMessages(rows);}catch{/* Stale messages never create new pulses. */}};
    void poll();const timer=window.setInterval(()=>void poll(),2500);
    return()=>{live=false;window.clearInterval(timer);};
  },[crewId,epoch,enabled]);
  return messages;
}

export function AgentTraffic({messages}:{messages:AgentMessage[]}){
  const crews=useAppStore(s=>s.crew);
  const label=(ref:string)=>crews.find(c=>`crew:${c.id}`===ref)?.name??crews.flatMap(c=>(c.agents??[]).map(a=>({ref:`agent:${a.id}`,name:`${c.name}/${a.name}`}))).find(a=>a.ref===ref)?.name??(ref==='core'?'Mavo Core':ref==='user'?'You':ref);
  return <details className="conversation-activity"><summary>{messages.length} recent handoffs</summary>
    {!messages.length&&<p>No work has been handed over yet. A dependency line is not a live message.</p>}
    {messages.slice(0,20).map(m=><details className="mt-3" key={`${m.id}:${m.recipient}`}><summary>{label(m.sender)} → {label(m.recipient)} · {m.kind.replaceAll('_',' ')} · {handoffStatus(m)}</summary>
      <p>{m.payload.summary}</p><small>{localTimestamp(m.created_at)} · {m.id.slice(0,8)} · Transport: {m.status}</small>
    </details>)}
  </details>;
}
