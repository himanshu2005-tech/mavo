import { LoaderCircle, Send, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import { CrewSetupCard } from "./CrewSetupCard";
import type { CoreStatus } from "../types";

export function NaturalCrewBuilder({onClose,onManual,onCreated,existingId}:{onClose:()=>void;onManual:()=>void;onCreated:(id:string)=>void;existingId?:string}) {
 const [session]=useState(()=>crypto.randomUUID());
 const [crewId,setCrewId]=useState(existingId);
 const crew=useAppStore(s=>[...s.crew,...s.setupDrafts].find(c=>c.id===crewId));
 const [core,setCore]=useState<CoreStatus|null>(null);
 const [text,setText]=useState("");
 const [messages,setMessages]=useState<Array<{role:string;content:string}>>([]);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState<string|null>(null);
 useEffect(()=>{void api.coreStatus().then(setCore).catch(e=>setError(String(e)));},[]);
 async function submit(e:FormEvent){e.preventDefault();if(!text.trim()||busy)return;const message=text.trim();setBusy(true);setError(null);try{const result=await api.coordinatorTurn(message,session,crewId);setMessages(result.messages);setText("");const setup=result.crew??result.draft;if(setup){useAppStore.getState().upsertCrew(setup);setCrewId(setup.id);if(result.crew)onCreated(result.crew.id);}}catch(e){setError(e instanceof Error?e.message:"Could not set up your Crew.");}finally{setBusy(false);}}
 return <div className="sheet-backdrop" role="dialog" aria-modal="true" aria-label="Create Crew"><div className="sheet"><div className="mb-5 flex items-start justify-between gap-4"><div><h2 className="text-lg font-semibold">{existingId?"Refine your Crew":"What should your Crew do?"}</h2><p className="mt-2 text-xs leading-6 text-zinc-500">Explain it in your own words. Mavo creates the setup and asks what it needs to know.</p></div><button aria-label="Close builder" className="btn-quiet !p-1" disabled={busy} onClick={onClose}><X size={16}/></button></div><div className="space-y-4">{messages.map((m,i)=><p key={i} className={`whitespace-pre-wrap text-xs leading-6 ${m.role==="user"?"message-user":"text-zinc-400"}`}>{m.content}</p>)}{crew&&<CrewSetupCard key={crew.id+JSON.stringify(crew.configuration_plan)} crew={crew}/>}</div><form onSubmit={submit} className="mt-5 space-y-3"><textarea aria-label="Crew responsibility" maxLength={8000} required className="form-control" rows={4} value={text} onChange={e=>setText(e.target.value)} placeholder={crew?"Answer a question or change its instructions...":"Highlight important college emails, ignore promotions, and show me urgent deadlines."}/>{error&&<p role="alert" className="notice error-notice">{error}</p>}{core&&!core.connected&&<p className="notice">{core.error}</p>}<div className="flex justify-between gap-3"><button type="button" className="btn-quiet" disabled={busy} onClick={onManual}>Advanced</button><div className="flex gap-2">{crew&&<button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>Done</button>}<button className="btn-primary" disabled={busy||!text.trim()||!core?.connected}>{busy?<LoaderCircle size={13} className="animate-spin"/>:<Send size={13}/>} {busy?"Working...":crew?"Send":"Create"}</button></div></div></form></div></div>;
}
