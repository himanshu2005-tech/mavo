import { useEffect, useState } from "react";
import { ExternalLink, LoaderCircle, Play } from "lucide-react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { CrewInstance } from "../types";

export function BrowserSignIn({crew}:{crew:Pick<CrewInstance,"id"|"name"|"status"|"responsibilities">}) {
  const initial=crew.responsibilities.match(/https:\/\/[^\s<>"\)]+/)?.[0].replace(/[.,;]+$/g,"")??(()=>{const bare=crew.responsibilities.match(/\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b/i)?.[1];return bare?"https://"+bare:"";})();
  const [url,setUrl]=useState(initial);
  const [busy,setBusy]=useState(false);
  const [opened,setOpened]=useState(false);
  const [message,setMessage]=useState<string|null>(null);
  const [error,setError]=useState<string|null>(null);
  useEffect(()=>{let active=true;void api.crewBrowserStatus(crew.id).then(s=>{if(active&&s.open&&s.signing_in){setOpened(true);setMessage(crew.status==="draft"?"Finish signing in, then Verify & add in setup.":"Finish signing in, then Resume Crew.");}}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[crew.id,crew.status]);
  async function open() {
    setBusy(true);setError(null);
    try {const result=await api.openCrewBrowser(crew.id,url.trim());setOpened(result.opened);setMessage(result.message);useAppStore.getState().setCrew(await api.crew());useAppStore.getState().setSetupDrafts(await api.crewDrafts());}
    catch(e){setError(e instanceof Error?e.message:"Could not open Mavo's browser.");}
    finally{setBusy(false);}
  }
  async function resume(){setBusy(true);setError(null);try{useAppStore.getState().upsertCrew(await api.startCrew(crew.id));setMessage("Crew resumed.");setOpened(false);}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <div className="rounded-lg border border-line p-4" aria-label={`Browser sign-in for ${crew.name}`}>
    <p className="text-xs font-medium">Mavo browser</p><p className="mt-1 text-xs leading-6 text-zinc-500">Sign in here once; it is Mavo’s own private browser, separate from your personal one. Work pauses while you sign in. No password exports or files are needed.</p>
    <label className="mt-2 block text-xs text-zinc-400">Site to open<input aria-label={`Browser URL for ${crew.name}`} type="url" className="form-control" placeholder="https://your-portal.example" value={url} onChange={e=>setUrl(e.target.value)} disabled={busy}/></label>
    <div className="mt-3 flex flex-wrap gap-2"><button type="button" className="btn-secondary" disabled={busy||!url.startsWith("https://")} onClick={()=>void open()}>{busy?<LoaderCircle size={13} className="animate-spin"/>:<ExternalLink size={13}/>}Open browser to sign in</button>{opened&&crew.status!=="draft"&&<button type="button" className="btn-primary" disabled={busy} onClick={()=>void resume()}><Play size={12}/>Resume Crew</button>}</div>
    {message&&<p role="status" className="mt-3 text-xs leading-6 text-zinc-400">{message}</p>}{error&&<p role="alert" className="notice error-notice mt-3">{error}</p>}
  </div>;
}
