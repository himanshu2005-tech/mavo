import { LoaderCircle, Send } from "lucide-react";
import { useState, type FormEvent } from "react";
import type { CrewInstance } from "../types";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
export function CrewReplyBox({crew}:{crew:CrewInstance}) {
 const [text,setText]=useState("");const [busy,setBusy]=useState(false);const [error,setError]=useState<string|null>(null);
 if(crew.configuration_plan?.draft&&crew.configuration_plan.phase!=="ready")return null;
 async function send(e:FormEvent){e.preventDefault();if(!text.trim()||busy)return;setBusy(true);setError(null);try{useAppStore.getState().upsertCrew(await api.replyCrew(crew.id,text.trim()));setText("");}catch(e){setError(e instanceof Error?e.message:"Could not send your reply.");}finally{setBusy(false);}}
 return <form onSubmit={send} className="mt-5 border-t border-line pt-4">{crew.question&&<p className="notice mb-3">{crew.question}</p>}<label className="block text-xs text-zinc-500">{crew.question?"Your answer":"Message this Crew"}<textarea className="form-control" rows={3} maxLength={4000} value={text} onChange={e=>setText(e.target.value)} placeholder={crew.question?"Answer its question…":"For example: also cover robotics news"}/></label>{error&&<p role="alert" className="notice error-notice mt-2">{error}</p>}<button className="btn-primary mt-3" disabled={busy||!text.trim()||crew.runtime_state==="working"}>{busy?<LoaderCircle size={12} className="animate-spin"/>:<Send size={12}/>}Send</button><p className="mt-2 text-[10px] text-zinc-500">Uses this Crew's agent connection and existing access.</p></form>;
}
