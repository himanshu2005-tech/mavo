import { useCallback, useEffect, useRef, useState } from 'react';
import { LoaderCircle, MessageCircle, Shield } from 'lucide-react';
import { api } from '../lib/api';
import { activityForScope, conciseActivity, type ConversationActivity as Activity, type ConfigEdit } from '../lib/orchestration';
import { useAppStore } from '../store/app';
import { Switch } from './CrewSetupCard';
import {ActionAccessEditor,type ActionRule} from './ActionAccessEditor';
import {TaskRunCard} from './TaskRunCard';
import {Report} from './Report';
import type {TaskRun} from '../types';

export function useConversationActivity(conversationId?:string, enabled = true) {
  const [rows,setRows]=useState<Activity[]>([]),[error,setError]=useState<string|null>(null);
  const epoch=useAppStore(s=>s.workspaceEpoch);
  const generation=useRef(0);
  const reload=useCallback(async()=>{
    if(!enabled)return;
    const request=++generation.current;
    const current=useAppStore.getState().workspaceEpoch;
    try{
      const next=await api.orchestrationEvents(conversationId);
      if(request===generation.current&&current===useAppStore.getState().workspaceEpoch){setRows(next);setError(null);}
    }catch(e){if(request===generation.current&&current===useAppStore.getState().workspaceEpoch)setError(e instanceof Error?e.message:'Activity unavailable');}
  },[conversationId,enabled]);
  useEffect(()=>{
    setRows([]);setError(null);if(!enabled)return;let live=true;
    const poll=async()=>{if(live&&!document.hidden)await reload();};
    void poll();const timer=window.setInterval(()=>void poll(),4000);
    return()=>{live=false;generation.current++;window.clearInterval(timer);};
  },[reload,epoch,enabled]);
  return {rows,error,reload};
}

export function configurationValue(value:unknown):string {
  if(value==null)return 'Not set';
  if(Array.isArray(value))return value.join(', ');
  if(typeof value==='object'&&'agents' in value&&Array.isArray(value.agents))return value.agents.map((a:{name?:string;role?:string})=>`${a.name??'Agent'}${a.role?` (${a.role})`:''}`).join(' → ');
  return typeof value==='object'?JSON.stringify(value):String(value);
}
export function ConfigurationDiff({edit}:{edit:ConfigEdit}) {
  return <div className="configuration-diff" aria-label="Configuration changes">
    {edit.diff.map(d=><p key={d.field}><span>{d.label}</span><span>{configurationValue(d.before)} → {configurationValue(d.after)}</span></p>)}
    <small>Applies to: {edit.scope==='persistent'?'all future runs':edit.scope==='run'?'this run or the next run only (expires in 24 hours)':edit.scope==='step'?'the next model call in the active run only':'future tasks'}</small>
  </div>;
}

export function ActivityCard({item,onChange}:{item:Activity;onChange:()=>void|Promise<void>}) {
  const [answer,setAnswer]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null);
  const [applied,setApplied]=useState<ConfigEdit|null>(null);
  const crew=useAppStore(s=>s.crew.find(c=>c.id===item.crew_id));
  const member=crew?.agents?.find(a=>`agent:${a.id}`===item.sender);
  const cap=item.payload.capability;
  const edit=applied??item.payload.edit;
  const [rule,setRule]=useState<ActionRule|null>(null);
  useEffect(()=>{
    if(!cap?.operation||!item.crew_id)return;
    let live=true;
    void api.actionPolicies(item.crew_id,true).then(result=>{if(live)setRule(result.actions.find(a=>a.operation===cap.operation)?.rule??null);}).catch(e=>{if(live)setError(String(e));});
    return()=>{live=false;};
  },[cap?.operation,item.crew_id]);
  async function decide(decision:'answer'|'grant'|'decline') {
    setBusy(true);setError(null);
    try{
      if(decision==='grant'&&cap?.operation&&rule&&item.crew_id)await api.saveActionPolicy(cap.operation,rule,item.crew_id);
      await api.answerActivity(item.id,item.revision,answer,decision);await onChange();
      useAppStore.getState().setCrew(await api.crew());
    }catch(e){setError(e instanceof Error?e.message:'Could not save this decision.');}
    finally{setBusy(false);}
  }
  async function change(rollback=false){
    if(!edit)return;setBusy(true);setError(null);
    try{setApplied(await (rollback?api.rollbackConfiguration(edit.id):api.applyConfiguration(edit.id)));await onChange();
      useAppStore.getState().setCrew(await api.crew());
    }catch(e){setError(e instanceof Error?e.message:'Could not update configuration.');}
    finally{setBusy(false);}
  }
  return <article className="conversation-activity" data-kind={item.kind}>
    <header>{cap?<Shield size={14}/>:<MessageCircle size={14}/>}<strong>{member?`${crew!.name}/${member.name}`:crew?.name??item.payload.name??'Mavo Core'}</strong><span>{item.kind.replaceAll('_',' ')}</span>{busy&&<LoaderCircle size={14} className="animate-spin"/>}</header>
    {/* A result is a report (often written in LaTeX): drawn, never shown as raw source. */}
    <Report compact truncate={false} text={String(item.payload.summary??'')}/>
    {item.task_id&&['blocked','failure','completion'].includes(item.kind)&&<ActivityTask id={item.task_id}/>}
    {item.kind==='approval_request'&&item.status==='pending'&&<button className="btn-secondary mt-3" onClick={()=>item.payload.action_request_id?useAppStore.getState().setPage('actions'):useAppStore.getState().openAgent(item.crew_id)}>Review {item.payload.action_request_id?'action':'handoff'}</button>}
    {cap&&<div className="capability-request">
      <p><strong>{cap.capability}</strong>{cap.operation?` · ${cap.operation}`:''}</p>
      {Object.entries(cap.scope).map(([key,values])=><p key={key}>{key}: {values.join(', ')||'Not specified'}</p>)}
      <small>{cap.risk==='read'?'Read-only':'Can change data'} · Until revoked for this Crew. This does not approve a specific dangerous action.</small>
      {item.status==='pending'&&cap.operation&&rule&&<div className="mt-3"><ActionAccessEditor operation={cap.operation} value={rule} onChange={setRule}/><button className="btn-secondary mt-3" disabled={busy||!rule.enabled} onClick={()=>void decide('grant')}>Save access & recheck</button></div>}
      {item.status==='pending'&&<div className="flex items-center justify-between gap-3 mt-3">
        {cap.setup==='grant'&&!cap.operation?<><span>Allow this access</span><Switch on={false} disabled={busy} onChange={()=>void decide('grant')}/></>:
          <button className="btn-secondary" onClick={()=>useAppStore.getState().setPage(cap.setup==='actions'?'actions':'resources')}>Review {cap.setup==='actions'?'action':'resource'} access</button>}
        <button className="btn-quiet" disabled={busy} onClick={()=>void decide('decline')}>Decline</button>
      </div>}
    </div>}
    {edit&&<><ConfigurationDiff edit={edit}/><div className="flex gap-2 mt-3">
      {edit.status==='proposed'&&<button className="btn-secondary" disabled={busy} onClick={()=>void change()}>Apply changes</button>}
      {edit.status==='applied'&&edit.scope==='persistent'&&<button className="btn-quiet" disabled={busy} onClick={()=>void change(true)}>Review rollback</button>}
      <small>{edit.status==='deferred'?'Saved · applies after the active run':edit.status}</small>
    </div></>}
    {item.status==='pending'&&item.kind==='question'&&<form className="mt-3" onSubmit={e=>{e.preventDefault();void decide('answer');}}>
      <label className="sr-only" htmlFor={`answer-${item.id}`}>Reply to {crew?.name??item.payload.name??'Mavo'} · {item.step_id??item.id.slice(0,8)}</label>
      <div className="flex gap-2"><input id={`answer-${item.id}`} className="field min-w-0 flex-1" maxLength={4000} value={answer} onChange={e=>setAnswer(e.target.value)} placeholder="Reply to this question…" disabled={busy}/><button className="btn-secondary" disabled={busy||!answer.trim()}>Reply</button></div>
    </form>}
    {error&&<p className="notice error-notice" role="alert">{error}</p>}
  </article>;
}

function ActivityTask({id}:{id:string}) {
  const [open,setOpen]=useState(false),[run,setRun]=useState<TaskRun|null>(null),[error,setError]=useState<string|null>(null);
  useEffect(()=>{
    if(!open)return;let live=true;
    const load=async()=>{try{const next=await api.task(id);if(live){setRun(next);setError(null);}}catch(e){if(live)setError(e instanceof Error?e.message:'Task unavailable');}};
    void load();const timer=window.setInterval(()=>void load(),4000);
    return()=>{live=false;window.clearInterval(timer);};
  },[id,open]);
  return <details className="activity-details" onToggle={e=>setOpen(e.currentTarget.open)}><summary>How it was done</summary>{open&&(run?<TaskRunCard run={run} details/>:<p role="status">{error??'Loading task…'}</p>)}</details>;
}

export function ConversationActivities({conversationId,workspace=false}:{conversationId?:string;workspace?:boolean}) {
  const {rows,error,reload}=useConversationActivity(conversationId, workspace||!!conversationId);
  const [open,setOpen]=useState(false);
  const [allPending,setAllPending]=useState(false);
  const concise=conciseActivity(activityForScope(rows,conversationId,workspace));
  const pending=concise.filter(r=>r.status==='pending'||r.kind==='configuration');
  const recorded=concise.filter(r=>!pending.includes(r));
  if(!workspace&&!conversationId)return null;
  if(!concise.length)return error?<p className="text-xs text-zinc-500" role="status">{error}</p>:null;
  return <section className="conversation-activities" aria-label="Crew activity">
    {(allPending?pending:pending.slice(0,3)).map(item=><ActivityCard key={item.id} item={item} onChange={reload}/>)}
    {pending.length>3&&<button className="btn-quiet" onClick={()=>setAllPending(!allPending)} aria-expanded={allPending}>{allPending?'Collapse requests':`Show ${pending.length-3} more requests`}</button>}
    {!!recorded.length&&<><button type="button" className="btn-quiet" aria-expanded={open} onClick={()=>setOpen(!open)}>{recorded.length} recent Crew updates</button>
      {open&&recorded.map(item=><ActivityCard key={item.id} item={item} onChange={reload}/>)}</>}
  </section>;
}
