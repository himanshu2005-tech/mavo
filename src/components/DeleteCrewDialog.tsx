import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { LoaderCircle } from "lucide-react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { CrewInstance } from "../types";

export function DeleteCrewDialog({crew,onClose}:{crew:CrewInstance;onClose:()=>void}) {
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const draft=crew.status==="draft";
  async function remove() {
    setBusy(true); setError(null);
    try {
      await api.deleteCrew(crew.id);
      useAppStore.getState().removeCrew(crew.id);
      try {
        const reminder=await invoke<{crew_name:string}|null>("pending_reminder");
        if(reminder?.crew_name===crew.name)await invoke("close_reminder_overlay");
      } catch { console.warn("Could not check the reminder window after deleting Crew."); }
      onClose();
    } catch(e) {setError(e instanceof Error?e.message:"Could not delete this Crew.");}
    finally {setBusy(false);}
  }
  return <div className="sheet-backdrop"><section className="sheet !w-[440px]" role="alertdialog" aria-modal="true" aria-labelledby="delete-crew-title" aria-describedby="delete-crew-description">
    <h2 id="delete-crew-title" className="text-lg font-semibold">{draft?"Discard setup?":"Delete Crew?"}</h2>
    <p className="mt-3 text-sm text-zinc-200">{crew.name}</p>
    <p id="delete-crew-description" className="mt-2 text-xs leading-6 text-zinc-400">{draft?"Removes this saved draft. Nothing has been activated.":"Stops this Crew and removes its saved work and access settings. External actions already sent cannot be undone."}</p>
    {error&&<p role="alert" className="notice error-notice mt-4">{error}</p>}
    <div className="mt-5 flex justify-end gap-2"><button autoFocus className="btn-secondary" disabled={busy} onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy} onClick={()=>void remove()}>{busy&&<LoaderCircle size={13} className="animate-spin"/>}{draft?"Discard setup":"Delete Crew"}</button></div>
  </section></div>;
}
