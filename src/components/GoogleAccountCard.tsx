import { invoke } from "@tauri-apps/api/core";
import { LoaderCircle, Workflow } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useGoogleConnection } from "../lib/useGoogleConnection";
import { useAppStore } from "../store/app";
import { permissionLabels } from "./PermissionPicker";
import type { GoogleCapability } from "../types";

export function GoogleAccountCard() {
  const google = useGoogleConnection();
  const [revoking, setRevoking] = useState(false);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const connected = !!google.status?.signed_in;
  const busy = google.busy || revoking;
  const capabilities = google.status?.capabilities ?? [];
  const receipt = google.status?.revocation;

  async function revoke() {
    setRevoking(true);
    google.setError(null);
    try {
      const result = await api.revokeGoogle();
      await google.refresh();
      const crews = await api.crew();
      if (!active.current) return;
      useAppStore.getState().setCrew(crews);
      if (result.requires_provider_action) {
        await invoke("open_google_url", { url: result.manage_url });
      }
    } catch (e) {
      google.setError(e instanceof Error ? e.message : "Could not revoke Google access.");
    } finally {
      setRevoking(false);
    }
  }

  return <section className="surface p-6" aria-label="Google Workspace">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <span className="crew-icon"><Workflow size={18} /></span>
        <div><h2 className="text-sm font-semibold">Google</h2>
          <p className="mt-1 text-xs text-zinc-500">{connected ? google.status?.email : "Gmail, Calendar, Tasks, Drive and Contacts"}</p>
        </div>
      </div>
      <span className="badge" aria-label="Google connection status">{revoking ? "Disconnecting…" : !google.status ? "Checking…" : connected ? "Connected" : "Not connected"}</span>
    </div>
    {connected && <details className="mt-4 text-xs" aria-label="Google access details">
      <summary className="cursor-pointer text-zinc-300">{connected ? "What Mavo can access" : "What you’re connecting"}</summary>
      {connected ? <ul className="mt-3 space-y-2 text-zinc-400">
        {capabilities.map(cap => <li key={cap}>{permissionLabels[cap] ?? cap}</li>)}
        {!capabilities.length && <li>No service permissions granted.</li>}
      </ul> : <ul className="mt-3 space-y-2 leading-5 text-zinc-400">
        <li>Gmail — read, draft and send email; archive, mark read or move mail to trash.</li>
        <li>Calendar and Tasks — read, create, update and delete entries.</li>
        <li>Drive — file names, types and links only; no document contents or changes.</li>
        <li>Contacts — read saved names, email addresses and phone numbers.</li>
      </ul>}
    </details>}
    <div className="mt-5 flex items-center gap-2">
      {connected ? <button className="btn-secondary" disabled={busy || google.pending} onClick={() => void revoke()}>{revoking && <LoaderCircle size={14} className="animate-spin" />}Revoke access</button>
        : <button className="btn-primary" disabled={!google.status?.configured || busy || google.pending} onClick={() => void google.connect({ capabilities: Object.keys(permissionLabels) as GoogleCapability[] })}>
          {(busy || google.pending) && <LoaderCircle size={14} className="animate-spin" />}{google.pending ? "Connecting…" : "Connect Google"}
        </button>}
      {google.pending && <button className="btn-quiet" disabled={busy} onClick={() => void google.cancel()}>Cancel</button>}
    </div>
    {google.pending && <p role="status" className="mt-3 text-xs text-zinc-400">Finish in your browser.</p>}
    {google.status && !google.status.configured && <p className="mt-3 text-xs text-zinc-500">Unavailable in this build.</p>}
    {!connected && receipt?.requires_provider_action && <p className="mt-3 text-xs leading-5 text-zinc-400">Disconnected here. <button className="underline" onClick={() => void invoke("open_google_url", { url: receipt.manage_url }).catch(e => google.setError(String(e)))}>Finish revoking on Google</button>.</p>}
    {(google.error || google.status?.error) && <p role="alert" className="notice error-notice mt-3">{google.error || google.status?.error}</p>}
  </section>;
}
