import { useState } from 'react';
import type { CrewAgent, CrewComposition, CrewInstance } from '../types';
import { AgentBot, type BotState } from './AgentBot';
import { identityColor, identityStyle } from '../lib/identityColors';
import { useAgentMessages } from './AgentTraffic';
import { useAppStore } from '../store/app';
import { parseTime } from '../lib/time';
import '../crew-members.css';

export function crewComposition(crew:CrewInstance):CrewComposition|undefined {
  const config=crew.configuration_plan as unknown as {composition?:CrewComposition;draft?:{composition?:CrewComposition}}|undefined;
  return config?.composition??config?.draft?.composition;
}

export function memberEdges(agents:CrewAgent[]) {
  const ids=new Map(agents.map(a=>[a.key,a.id]));
  return agents.flatMap(a=>(a.definition.depends_on??[]).flatMap(key=>ids.has(key)?[{from:ids.get(key)!,to:a.id}]:[]));
}

/** Collapsed by default; internal membership never becomes hundreds of top-level map nodes. */
export function CrewMembers({crew,onSelect,expanded=false,onEdge,onModel}:{crew:CrewInstance;onSelect?:(id:string)=>void;expanded?:boolean;onEdge?:(from:string,to:string)=>void;onModel?:(key:string,routeId:string|null)=>void}) {
  const [open,setOpen]=useState(expanded);
  const agents=crew.agents?.filter(a=>a.status==='active')??[];
  const composition=crewComposition(crew);
  const routes=useAppStore(s=>s.routes);
  const modelOf=(id?:string|null)=>routes.find(r=>r.id===(id??crew.route_id))?.name;
  const rows=agents.length?agents:composition?.agents.map(a=>({...a,id:a.key,crew_id:crew.id,status:'active',runtime_state:'draft',definition:a,execution:{},checkpoint:null,route_id:a.route_id??null} as CrewAgent))??[];
  const messages=useAgentMessages(crew.id,open&&agents.length>1);
  if(!rows.length)return null;
  const edges=memberEdges(rows);
  const now=Date.now();
  const activeEdges=new Set(edges.filter(e=>messages.some(m=>m.sender===`agent:${e.from}`&&m.recipient===`agent:${e.to}`&&now-parseTime(m.created_at).getTime()>=0&&now-parseTime(m.created_at).getTime()<8000)).map(e=>`${e.from}|${e.to}`));
  const names=new Map(rows.map(a=>[a.id,a.name]));
  const levels=flowLevels(rows,edges);
  const tint=(a:CrewAgent)=>identityStyle(identityColor(`member:${a.id}`));
  return <section className="crew-members" aria-label={`${crew.name} agents`}>
    <button className="crew-members-toggle" aria-expanded={open} onClick={()=>setOpen(!open)}>
      <span className="crew-bots-stack" aria-hidden="true">{rows.slice(0,5).map(a=><span key={a.id} className="crew-bot-face sm" style={tint(a)}><AgentBot seed={a.id} state={memberBot(a,crew)}/></span>)}</span>
      <span className="crew-members-count">{rows.length} {rows.length===1?'agent':'agents'}</span>
      <span className="crew-members-more">{open?'Collapse':'Show roles'}</span>
    </button>
    {open&&<>
      <ol className="crew-flow">{levels.map((level,i)=><li key={i} className="crew-flow-level">
        {i>0&&<span className="crew-flow-link" aria-hidden="true" data-live={level.some(a=>edges.some(e=>e.to===a.id&&activeEdges.has(`${e.from}|${e.to}`)))||level.some(a=>memberBot(a,crew)==='working')}><i/></span>}
        <div className="crew-flow-row">{level.map(a=>{
          const from=edges.filter(e=>e.to===a.id);
          const model=modelOf(a.route_id), state=memberBot(a,crew);
          return <div key={a.id} className="crew-bot-card" data-state={state} style={tint(a)}
            role={onSelect?'button':undefined} tabIndex={onSelect?0:undefined} aria-label={`${a.name}: ${STATE_WORDS[state]}`}
            onClick={()=>onSelect?.(a.id)} onKeyDown={e=>{if(onSelect&&(e.key==='Enter'||e.key===' ')){e.preventDefault();onSelect(a.id);}}}>
            <span className="crew-bot-face"><AgentBot seed={a.id} state={state}/></span>
            <span className="crew-bot-text">
              <strong>{a.name}{composition?.lead===a.key&&<em className="crew-bot-lead">Lead</em>}</strong>
              <small>{a.role}</small>
              {onEdge&&from.length>0&&<span className="crew-bot-from">{from.map(e=><button key={e.from} type="button" onClick={ev=>{ev.stopPropagation();onEdge(e.from,e.to);}}>from {names.get(e.from)}</button>)}</span>}
            </span>
            <span className="crew-bot-meta"><span className="crew-bot-state">{STATE_WORDS[state]}</span>{onModel&&routes.length>0
              ? <select className="crew-bot-model-pick" aria-label={`Model for ${a.name}`} value={a.route_id??''} onClick={ev=>ev.stopPropagation()} onChange={ev=>onModel(a.key,ev.target.value||null)}>
                  <option value="">Crew's model</option>{routes.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}
                </select>
              : model&&<span className="crew-bot-model">{model}</span>}</span>
          </div>;
        })}</div>
      </li>)}</ol>
      {composition&&<small className="crew-flow-note">Each agent sees only what its own step needs, and can use its own model.</small>}
    </>}
  </section>;
}

const STATE_WORDS:Record<BotState,string>={idle:'Draft',ready:'Waiting',working:'Working',needs:'Needs you',stopped:'Stopped',done:'Done'};

/** How a member's bot looks: drafting, waiting its turn, working, stuck, done or stopped. */
function memberBot(a:CrewAgent,crew:CrewInstance):BotState {
  const s=a.runtime_state;
  if(crew.status==='draft'||s==='draft')return 'idle';
  if(s==='working'||s==='running')return 'working';
  if(['needs_input','blocked','error','failed'].includes(s))return 'needs';
  if(s==='done')return 'done';
  return crew.is_running?'ready':'stopped';
}

/** Members in the order work flows: each step after the ones it waits for, side by side when
 *  they run together. */
export function flowLevels(rows:CrewAgent[],edges:{from:string;to:string}[]):CrewAgent[][] {
  const level=new Map<string,number>();
  const depth=(id:string,seen=new Set<string>()):number=>{
    if(level.has(id))return level.get(id)!;
    if(seen.has(id))return 0;
    seen.add(id);
    const before=edges.filter(e=>e.to===id).map(e=>depth(e.from,seen)+1);
    const d=before.length?Math.max(...before):0;
    level.set(id,d);return d;
  };
  rows.forEach(a=>depth(a.id));
  const out:CrewAgent[][]=[];
  rows.forEach(a=>{const d=level.get(a.id)??0;(out[d]??=[]).push(a);});
  return out.filter(Boolean);
}

export function MemberPanel({crew,agent,onClose}:{crew:CrewInstance;agent:CrewAgent;onClose:()=>void}) {
  const routes=useAppStore(s=>s.routes);
  const route=routes.find(r=>r.id===(agent.route_id??crew.route_id));
  const grants=agent.definition.capabilities??crew.permissions;
  return <section className="member-detail">
    <button className="btn-quiet" onClick={onClose}>Close</button>
    <small>{crew.name}</small><h3>{agent.name}</h3><p>{agent.role} · {agent.runtime_state.replaceAll('_',' ')}</p>
    <h4>Assignment</h4><p>{agent.instructions}</p>
    <h4>Model</h4><p>{route?.name??'Chosen when it runs'}</p>
    <h4>Completion criteria</h4><p>{agent.definition.completion_criteria??'Existing Crew responsibility'}</p>
    <h4>Restricted access</h4>{grants.map(cap=><p key={cap}>{cap}<small> · {crew.permissions.includes(cap)?'Within Crew grant':'Not granted'}</small></p>)}
    {!!agent.definition.depends_on?.length&&<><h4>Depends on</h4><p>{agent.definition.depends_on.join(', ')}</p></>}
    {agent.execution.verification&&<><h4>Verification</h4><p>{agent.execution.verification.reason}</p><small>Objective: {agent.execution.verification.objective.replaceAll('_',' ')}</small></>}
    {(agent.execution.output||agent.execution.error||agent.execution.question)&&<><h4>Latest work</h4><p>{agent.execution.output??agent.execution.error??agent.execution.question}</p></>}
    {agent.execution.task_id&&<small>Task {agent.execution.task_id.slice(0,8)} · {agent.execution.step_id}</small>}
    <p className="mt-4 text-xs text-zinc-500">Address this member with @{crew.name}/{agent.name} in chat. Private working context is not shared with other agents.</p>
  </section>;
}
