import { ChevronDown, FolderOpen, LoaderCircle, Pause, Play, Save, SlidersHorizontal, Terminal, Globe, AppWindow, Mail, CalendarDays, ListTodo } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { PageHeader } from "../components/PageHeader";
import { ActionAccessEditor, type ActionRule } from "../components/ActionAccessEditor";
import { BrowserSignIn } from "../components/BrowserSignIn";
import { CrewArt, HUES } from "../components/CrewArt";
import { Chip, type Tone } from "../components/Studio";
import { api } from "../lib/api";
import { SCHEDULES, automationNameFor, bulkSummary, isLocal, scheduleLabel } from "../lib/actionRuleInput";
import { useAppStore } from "../store/app";
import type { AutomationChoice, AutomationInfo, CrewInstance, ProjectInfo, RunApproval } from "../types";
export type { ActionRule } from "../components/ActionAccessEditor";

type SavedRule = ActionRule & {origin?: string};
interface Entry {operation:string;group:string;label:string;authorized?:boolean;rule:SavedRule}
type Owner = Pick<CrewInstance,"id"|"name"|"status"|"responsibilities">;
const icons:Record<string,typeof Terminal>={Terminal,Browser:Globe,Windows:AppWindow,Projects:FolderOpen,Mail,Calendar:CalendarDays,Tasks:ListTodo,GitHub:Globe};
const accessLabel=(rule:ActionRule)=>!rule.enabled?"Off":rule.mode==="restricted"?"With limits":"Without limits";

export function ActionsPage(){
  const {crew,setupDrafts}=useAppStore();
  const actors=[...crew,...setupDrafts.filter(d=>!d.configuration_plan?.questions.length)];
  const [actor,setActor]=useState(()=>{const id=sessionStorage.getItem("mavo-action-actor")??"";sessionStorage.removeItem("mavo-action-actor");return id;});
  const current=useRef(actor);current.current=actor;
  const [data,setData]=useState<{paused:boolean;actions:Entry[]}|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const [done,setDone]=useState<string|null>(null);
  const selected=actors.find(c=>c.id===actor);
  async function load(){try{const value=await api.actionPolicies(actor||undefined);if(current.current===actor){setData(value);setError(null);}}catch(e){setError(String(e));}}
  useEffect(()=>{let active=true;setData(null);void api.actionPolicies(actor||undefined).then(v=>{if(active){setData(v);setError(null);}}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[actor]);
  async function pause(){setBusy(true);try{if(data?.paused)await api.resumeActions();else await api.pauseActions();await load();useAppStore.getState().setCrew(await api.crew());}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <><PageHeader eyebrow="Workspace" title="Actions" description="What Mavo is allowed to do." action={<div className="flex items-center gap-2"><SetAll actor={actor} owner={selected} disabled={!data} onDone={message=>{setDone(message);void load();void api.crew().then(c=>useAppStore.getState().setCrew(c)).catch(()=>undefined);}}/><button className="btn-secondary" disabled={busy||!data} onClick={()=>void pause()}>{data?.paused?<Play size={13}/>:<Pause size={13}/>} {data?.paused?"Resume":"Pause all"}</button></div>}/>
    <div className="page-content space-y-6">
      <label className="flex items-center gap-3 text-xs text-zinc-500">View<select aria-label="Action access view" className="form-control !mt-0 !w-auto" value={actor} onChange={e=>setActor(e.target.value)}><option value="">Defaults &amp; all Crew</option>{actors.map(c=><option value={c.id} key={c.id}>{c.name}{c.status==="draft"?" · Draft":""}</option>)}</select></label>
      <RunApprovals/>
      {done&&<p role="status" className="notice">{done}</p>}
      {data?.paused&&<p className="notice">Paused. Resume, then run the Crew you want.</p>}
      {error&&<p role="alert" className="notice error-notice">{error}</p>}
      {!data&&!error&&<p role="status" className="flex gap-2 text-xs"><LoaderCircle size={13} className="animate-spin"/>Loading...</p>}
      {data&&<div className="group-grid">{[...new Set(data.actions.map(a=>a.group))].map(group=><GroupCard key={actor+group} group={group} entries={data.actions.filter(a=>a.group===group)} actor={actor} owner={selected} onSaved={()=>void load()}/>)}</div>}
    </div></>;
}

// What each group of actions is, in a few words, and which picture it wears.
const GROUPS:Record<string,{scene:string;note:string}>={
  Terminal:{scene:"coding",note:"Run commands on this computer"},
  Browser:{scene:"research",note:"Open, read and use websites"},
  Windows:{scene:"computer",note:"Work with apps on this computer"},
  Projects:{scene:"operator",note:"Make folders and files, run scripts (asks first)"},
  Mail:{scene:"communication",note:"Read and send email"},
  Calendar:{scene:"schedule",note:"Read and change events"},
  Tasks:{scene:"personal-admin",note:"Read and change tasks"},
  GitHub:{scene:"project",note:"Read repositories and issues"},
};
const toneOf=(rule:ActionRule):Tone=>!rule.enabled?"off":rule.mode==="restricted"?"limit":"on";

/** One group of actions as a card you open: its picture, how much of it is on, then the rows. */
function GroupCard({group,entries,actor,owner,onSaved}:{group:string;entries:Entry[];actor:string;owner?:Owner;onSaved:()=>void}){
  const [open,setOpen]=useState(false);
  const meta=GROUPS[group]??{scene:"operator",note:""};
  const on=entries.filter(e=>e.rule.enabled).length;
  const title=["Mail","Calendar","Tasks"].includes(group)?"Google "+group:group;
  return <section className="group-card" data-open={open} style={{["--hue" as string]:HUES[meta.scene]}}>
    <button className="group-top" aria-expanded={open} aria-label={`${title}, ${on} of ${entries.length} on`} onClick={()=>setOpen(!open)}>
      <div className="tile-art" aria-hidden="true"><CrewArt template={meta.scene}/></div>
      <div className="min-w-0 flex-1 py-3"><h2>{title}</h2><p>{meta.note}</p><div className="mt-2"><Chip tone={on?"on":"off"}>{on} of {entries.length} on</Chip></div></div>
      <ChevronDown className="caret" size={18}/>
    </button>
    {open&&<div className="group-rows">{entries.map(entry=><ActionRow key={actor+entry.operation} entry={entry} owner={owner} onSaved={onSaved}/>)}{group==="Projects"&&<><ProjectList/><AutomationList/></>}</div>}
  </section>;
}

/** Set every action to one level at once, for the defaults or for the Crew being viewed. Anything
 *  that cannot be allowed with limits yet (no site, folder or app list) is left as it was and named. */
function SetAll({actor,owner,disabled,onDone}:{actor:string;owner?:Owner;disabled:boolean;onDone:(message:string)=>void}){
  const [open,setOpen]=useState(false);
  const [sure,setSure]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [at,setAt]=useState<{top:number;left:number}>({top:0,left:0});
  const button=useRef<HTMLButtonElement>(null);
  const menu=useRef<HTMLDivElement>(null);
  // The menu lives on the page, not inside the header banner, which clips what hangs below it.
  useEffect(()=>{if(!open)return;const close=(e:Event)=>{if(e instanceof KeyboardEvent?e.key==="Escape":!menu.current?.contains(e.target as Node)&&!button.current?.contains(e.target as Node)){setOpen(false);setSure(false);}};document.addEventListener("keydown",close);document.addEventListener("mousedown",close);return()=>{document.removeEventListener("keydown",close);document.removeEventListener("mousedown",close);};},[open]);
  function toggle(){const rect=button.current?.getBoundingClientRect();if(rect)setAt({top:rect.bottom+8,left:Math.max(12,Math.min(rect.left,window.innerWidth-304))});setOpen(!open);setSure(false);setError(null);}
  async function apply(mode:"off"|"limited"|"unrestricted"){setBusy(true);setError(null);try{const result=await api.setAllActions(mode,actor||undefined,mode==="unrestricted");onDone((owner?owner.name+": ":"Defaults: ")+bulkSummary(result));setOpen(false);setSure(false);}catch(e){setError(e instanceof Error?e.message:"Could not change the settings.");}finally{setBusy(false);}}
  return <div className="set-all"><button ref={button} className="btn-secondary" aria-haspopup="menu" aria-expanded={open} disabled={disabled} onClick={toggle}><SlidersHorizontal size={13}/> Set all</button>
    {open&&createPortal(<div ref={menu} role="menu" aria-label="Set all actions" className="set-all-menu" style={{top:at.top,left:at.left}}><p>For {owner?owner.name:"defaults and new Crew"}</p>
      <button role="menuitem" className="item" disabled={busy} onClick={()=>void apply("off")}>Turn everything off<small>Stops all of it at once</small></button>
      <button role="menuitem" className="item" disabled={busy} onClick={()=>void apply("limited")}>Allow all, with limits<small>Skips what still needs a site, folder or app list</small></button>
      {!sure?<button role="menuitem" className="item" disabled={busy} onClick={()=>setSure(true)}>Allow all, without limits…<small>Every action, no scope lists</small></button>
        :<div className="sure"><span>This lets {owner?owner.name:"every new Crew"} use every action without site, folder or app limits. Running a script still asks each time.</span><div className="flex gap-2"><button className="btn-primary" disabled={busy} onClick={()=>void apply("unrestricted")}>{busy&&<LoaderCircle size={12} className="animate-spin"/>}Yes, allow everything</button><button className="btn-quiet" onClick={()=>setSure(false)}>Cancel</button></div></div>}
      {error&&<p role="alert" className="notice error-notice m-2">{error}</p>}
    </div>,document.body)}
  </div>;
}

/** Scripts a Crew wants to run. Each is shown in full; running it is the person's decision, and it
 *  runs exactly these scripts (the server checks their hash again). */
function RunApprovals(){
  const [items,setItems]=useState<RunApproval[]>([]);
  const [trust,setTrust]=useState<Record<string,boolean>>({});
  const [keep,setKeep]=useState<Record<string,AutomationChoice|undefined>>({});
  const [busy,setBusy]=useState<string|null>(null);
  const [error,setError]=useState<string|null>(null);
  async function load(){try{setItems(await api.runApprovals());}catch{/* the list simply stays as it was */}}
  useEffect(()=>{void load();const t=window.setInterval(()=>void load(),3000);return()=>window.clearInterval(t);},[]);
  async function answer(item:RunApproval,run:boolean){setBusy(item.id);setError(null);try{if(run)await api.approveRun(item.id,item.sha256??"",!!trust[item.id],keep[item.id]);else await api.rejectAction(item.id);await load();}catch(e){setError(e instanceof Error?e.message:"Could not send your answer.");await load();}finally{setBusy(null);}}
  if(!items.length)return null;
  return <section aria-label="Waiting for you" className="space-y-3"><h2 className="text-sm font-semibold">Waiting for you</h2>
    {error&&<p role="alert" className="notice error-notice">{error}</p>}
    {items.map(item=>{const tool=item.tool==="blender"?"Blender":"Python";const what=item.script??item.file??"a script";return <article key={item.id} className="surface run-card" aria-label={`Run ${what}`}>
      <div><h3 className="text-[15px] font-semibold">Run {what} with {tool}</h3><p className="mt-1 text-xs text-zinc-500">{item.crew_name??"Your request"} · project {item.project}{item.show?" · opens its window":""}</p></div>
      {item.error?<p role="alert" className="notice error-notice">{item.error}</p>:<>
        {item.changed&&<p role="alert" className="notice error-notice">The scripts changed after this was requested. Deny it, and ask for the run again.</p>}
        <div className="space-y-3">{item.sources?.map((source,index)=><div key={source.path}><p className="script-name">{source.path}{index>0?" · also covered by this approval":""}{source.truncated?" · shortened":""}</p><pre className="script-box" tabIndex={0} aria-label={"Script "+source.path}>{source.text}</pre></div>)}</div>
        <p className="text-[11.5px] leading-5 text-zinc-500">It runs with your Windows account's rights, so read it first.{item.args?.length?" Arguments: "+item.args.join(" ")+".":""}</p>
        <label className="flex items-center gap-2 text-xs text-zinc-400"><input type="checkbox" checked={!!trust[item.id]} onChange={e=>setTrust({...trust,[item.id]:e.target.checked})}/>Trust this project: run its scripts without asking</label>
        <div className="space-y-2"><label className="flex items-center gap-2 text-xs text-zinc-400"><input type="checkbox" checked={!!keep[item.id]} onChange={e=>setKeep({...keep,[item.id]:e.target.checked?{name:automationNameFor(item.script??item.file,item.project),every_minutes:null,notify:"failure"}:undefined})}/>Keep as an automation: runs again with no AI, only these exact scripts</label>
          {keep[item.id]&&<div className="flex flex-wrap items-center gap-2 pl-6"><input className="form-control !mt-0 !w-44" aria-label="Automation name" value={keep[item.id]!.name} maxLength={40} onChange={e=>setKeep({...keep,[item.id]:{...keep[item.id]!,name:e.target.value}})}/>
            <select className="form-control !mt-0 !w-auto" aria-label="How often" value={String(keep[item.id]!.every_minutes)} onChange={e=>setKeep({...keep,[item.id]:{...keep[item.id]!,every_minutes:e.target.value==="null"?null:Number(e.target.value)}})}>{SCHEDULES.map(o=><option key={o.label} value={String(o.minutes)}>{o.label}</option>)}</select>
            <select className="form-control !mt-0 !w-auto" aria-label="Tell me" value={keep[item.id]!.notify} onChange={e=>setKeep({...keep,[item.id]:{...keep[item.id]!,notify:e.target.value as AutomationChoice["notify"]}})}><option value="failure">Tell me if it fails</option><option value="always">Tell me every time</option><option value="never">Never tell me</option></select></div>}</div></>}
      <div className="flex gap-2"><button className="btn-primary" disabled={busy===item.id||!!item.error||item.changed||!item.sha256} onClick={()=>void answer(item,true)}>{busy===item.id&&<LoaderCircle size={12} className="animate-spin"/>}Run</button><button className="btn-secondary" disabled={busy===item.id} onClick={()=>void answer(item,false)}>Deny</button></div>
    </article>;})}
  </section>;
}

/** The projects Mavo has made, and which of them may run scripts without asking. */
function ProjectList(){
  const [info,setInfo]=useState<{folder:string|null;error?:string;projects:ProjectInfo[]}|null>(null);
  const [error,setError]=useState<string|null>(null);
  async function load(){try{setInfo(await api.projects());setError(null);}catch(e){setError(e instanceof Error?e.message:"Could not list projects.");}}
  useEffect(()=>{void load();},[]);
  async function flip(project:ProjectInfo){try{await api.trustProject(project.project,!project.trusted);await load();}catch(e){setError(e instanceof Error?e.message:"Could not change this.");}}
  return <div className="action-row space-y-3" aria-label="Projects"><div><h3 className="text-[13.5px] font-medium">Your projects</h3><p className="mt-1 text-[11.5px] text-zinc-500">{info?.folder?"Kept in "+info.folder:"None yet."}</p></div>
    {(error||info?.error)&&<p role="alert" className="notice error-notice">{error??info?.error}</p>}
    {info?.projects.map(project=><div key={project.project} className="flex items-center justify-between gap-3"><span className="min-w-0 text-[13px]">{project.project}<span className="ml-2 text-[11.5px] text-zinc-500">{project.files} file{project.files===1?"":"s"}</span></span><button className={project.trusted?"btn-secondary":"btn-quiet"} aria-pressed={project.trusted} onClick={()=>void flip(project)}>{project.trusted?"Runs without asking":"Asks each time"}</button></div>)}
    {info&&!info.projects.length&&!info.error&&<p className="text-xs text-zinc-500">A Crew with these actions creates one when you ask it to build something.</p>}
  </div>;
}

function ActionRow({entry,owner,onSaved}:{entry:Entry;owner?:Owner;onSaved:()=>void}){
  const [open,setOpen]=useState(false);
  return <div className="action-row"><button className="flex w-full items-center justify-between gap-3 text-left" aria-expanded={open} aria-label={"Configure "+entry.label} onClick={()=>setOpen(!open)}><span className="text-[13.5px] font-medium">{entry.label}</span><span className="ml-auto"><Chip tone={toneOf(entry.rule)}>{!owner?"Default: ":""}{accessLabel(entry.rule)}</Chip></span><ChevronDown size={13} className={open?"rotate-180":""}/></button>
    {open&&<div className="mt-4 space-y-5 border-t border-line pt-4"><RuleEditor entry={entry} owner={owner} onSaved={onSaved}/>{!owner&&<Assignments operation={entry.operation} label={entry.label}/>}</div>}
  </div>;
}

function RuleEditor({entry,owner,onSaved}:{entry:Entry;owner?:Owner;onSaved:()=>void}){
  const [rule,setRule]=useState(entry.rule);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [saved,setSaved]=useState(false);
  useEffect(()=>{setRule(entry.rule);},[entry.rule]);
  async function save(){setBusy(true);setError(null);try{await api.saveActionPolicy(entry.operation,rule,owner?.id);if(owner){useAppStore.getState().setCrew(await api.crew());useAppStore.getState().setSetupDrafts(await api.crewDrafts());}setSaved(true);onSaved();}catch(e){setError(e instanceof Error?e.message:"Could not save access.");}finally{setBusy(false);}}
  return <fieldset className="min-w-0 space-y-3 rounded-lg border border-line p-4" aria-label={owner?entry.label+" for "+owner.name:"Defaults for "+entry.label}><legend className="px-1 text-xs font-medium text-zinc-200">{owner?owner.name:"Defaults for new Crew"}</legend>
    <p className="text-[11px] text-zinc-500">{owner?(owner.status==="draft"?"Setup draft · not activated":accessLabel(entry.rule)):"Also applies to your manual actions. Changing this does not alter existing Crew."}</p>
    <ActionAccessEditor key={JSON.stringify(entry.rule)} operation={entry.operation} value={rule} onChange={v=>{setRule(v);setSaved(false);}}/>
    {entry.authorized===false&&!isLocal(entry.operation)&&<p className="notice">This Crew also needs matching account access in its setup.</p>}
    <div className="flex items-center gap-3"><button className="btn-primary" disabled={busy} onClick={()=>void save()}>{busy?<LoaderCircle size={12} className="animate-spin"/>:<Save size={12}/>}Save</button>{saved&&<span role="status" className="text-xs text-zinc-500">Saved</span>}</div>
    {owner&&entry.operation==="browser.open"&&entry.rule.enabled&&<BrowserSignIn crew={owner}/>}
    {error&&<p role="alert" className="notice error-notice">{error}</p>}
  </fieldset>;
}

function Assignments({operation,label}:{operation:string;label:string}){
  const [items,setItems]=useState<Awaited<ReturnType<typeof api.actionAssignments>>|null>(null);
  const [error,setError]=useState<string|null>(null);
  async function load(){try{setItems(await api.actionAssignments(operation));setError(null);}catch(e){setError(String(e));}}
  useEffect(()=>{let active=true;void api.actionAssignments(operation).then(v=>{if(active)setItems(v);}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[operation]);
  return <div className="space-y-4" aria-label={"Crew access for "+label}><h3 className="text-xs font-medium">Crew access</h3>{error&&<p role="alert" className="notice error-notice">{error}</p>}{!items&&!error&&<p className="text-xs text-zinc-500">Loading Crew access...</p>}{items?.length===0&&<p className="text-xs text-zinc-500">No Crew ready for access yet. Finish the setup questions first.</p>}{items?.map(item=><RuleEditor key={item.id} owner={item} entry={{operation,label,group:"",authorized:item.authorized,rule:item.rule}} onSaved={()=>void load()}/>)}</div>;
}

/** Scripts the person approved and chose to keep: when they last ran, and a way to run or remove them. */
function AutomationList(){
  const [items,setItems]=useState<AutomationInfo[]|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [busy,setBusy]=useState<string|null>(null);
  async function load(){try{setItems(await api.automations());setError(null);}catch(e){setError(e instanceof Error?e.message:"Could not list automations.");}}
  useEffect(()=>{void load();const t=window.setInterval(()=>void load(),5000);return()=>window.clearInterval(t);},[]);
  async function act(item:AutomationInfo,remove:boolean){setBusy(item.slug);setError(null);try{if(remove)await api.removeAutomation(item.name);else await api.runAutomation(item.name);await load();}catch(e){setError(e instanceof Error?e.message:"Could not do that.");}finally{setBusy(null);}}
  const tone=(status:AutomationInfo["last_status"])=>status==="ok"?"on":status==="failed"||status==="changed"||status==="blocked"?"warn":"off";
  const word=(status:AutomationInfo["last_status"])=>status==="ok"?"Worked":status==="failed"?"Failed":status==="changed"?"Scripts changed: approve again":status==="blocked"?"Blocked":"Not run yet";
  return <div className="action-row space-y-3" aria-label="Automations"><div><h3 className="text-[13.5px] font-medium">Automations</h3><p className="mt-1 text-[11.5px] text-zinc-500">Scripts you approved and kept. They run with no AI, and stop if the scripts change.</p></div>
    {error&&<p role="alert" className="notice error-notice">{error}</p>}
    {items?.map(item=><div key={item.slug} className="space-y-1"><div className="flex items-center justify-between gap-3"><span className="min-w-0 text-[13px]">{item.name}<span className="ml-2 text-[11.5px] text-zinc-500">{scheduleLabel(item.every_minutes)}</span></span><span className="flex items-center gap-2"><Chip tone={tone(item.last_status)}>{word(item.last_status)}</Chip><button className="btn-quiet" disabled={busy===item.slug} onClick={()=>void act(item,false)}>Run now</button><button className="btn-quiet" disabled={busy===item.slug} aria-label={"Remove "+item.name} onClick={()=>void act(item,true)}>Remove</button></span></div>{item.last_output&&<p className="truncate text-[11.5px] text-zinc-500" title={item.last_output}>{item.last_output.split("\n").filter(Boolean).slice(-1)[0]}</p>}</div>)}
    {items&&!items.length&&<p className="text-xs text-zinc-500">When you approve a script run, you can choose to keep it here.</p>}
  </div>;
}
