import { Bell, KeyRound, LoaderCircle, Settings2 } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useGoogleConnection } from "../lib/useGoogleConnection";
import { useAppStore } from "../store/app";
import type { CrewInstance, GoogleCapability, GoogleStatus } from "../types";
import { permissionLabels } from "./PermissionPicker";

const SERVICE: Record<string, string> = { gmail: "Gmail", calendar: "Calendar", tasks: "Tasks", drive: "Drive", contacts: "Contacts", youtube: "YouTube" };
const WRITES = /:(send|modify|write)$/;

/** What an added agent still waits on, from its setup's own list: the reads it asked for, its
 *  desktop alerts, a Google connection, and anything else (which its settings hold). */
export function accessNeeds(crew: Pick<CrewInstance, "configuration_plan">) {
  const missing = (crew.configuration_plan as { missing?: string[] } | undefined)?.missing ?? [];
  const reads: GoogleCapability[] = [], other: string[] = [];
  let alerts = false, connect = false;
  for (const item of missing) {
    const cap = item.startsWith("Allow ") ? item.slice(6) : "";
    if (cap in permissionLabels && !WRITES.test(cap)) reads.push(cap as GoogleCapability);
    else if (item === "Allow desktop updates") alerts = true;
    else if (item === "Connect the required Google services") connect = true;
    else other.push(item);
  }
  return { reads, alerts, connect, other };
}

/** Whether the room shows the card: it waits on setup, or Google ended its sign-in. */
export function needsAccess(crew: CrewInstance, google: GoogleStatus | null): boolean {
  return crew.runtime_state === "needs_setup" || !!google?.expired;
}

function GoogleMark() {
  return <svg viewBox="0 0 18 18" width="15" height="15" aria-hidden="true">
    <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
    <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
    <path fill="#FBBC05" d="M3.97 10.72A5.41 5.41 0 0 1 3.68 9c0-.6.1-1.18.29-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.05l3.01-2.33z" />
    <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
  </svg>;
}

/** In an agent's room, what it needs before it can work, each with the one button that gives
 *  it: "Reconnect Google" when Google ended the sign-in, "Connect Google" for a service not
 *  connected yet, then "Allow" for the reads it asked for. Acting and its limits stay in its
 *  settings. */
export function NeedsAccess({ crew, onSettings, google: given }: { crew: CrewInstance; onSettings: () => void; google?: ReturnType<typeof useGoogleConnection> }) {
  const own = useGoogleConnection(!given);
  const google = given ?? own;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { reads, alerts, connect, other } = accessNeeds(crew);
  const status = google.status;
  const expired = !!status?.expired;
  const have = new Set(status?.capabilities ?? []);
  const notConnected = reads.filter(c => !have.has(c));
  const needsGoogle = expired || notConnected.length > 0 || (connect && !!status && !status.signed_in);
  const canAllow = !needsGoogle && (reads.length > 0 || alerts || connect);

  async function allow() {
    setBusy(true); setError(null);
    try { useAppStore.getState().upsertCrew(await api.allowCrew(crew.id, { capabilities: reads, notifications: alerts })); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not allow it."); }
    finally { setBusy(false); }
  }
  // Reconnecting asks again for everything it had, so no agent loses what it was given.
  const reconnect = () => void google.connect({ capabilities: [...new Set([...(status?.capabilities ?? []), ...reads])] as GoogleCapability[] });

  const title = expired ? "Google access expired" : needsGoogle ? "It needs Google access" : "It needs your OK";
  const line = expired ? "Google ended the sign-in. Reconnect, and it carries on." : needsGoogle ? "Connect Google, then allow it." : "Allow this, and it starts.";
  return <section className="needs-access" aria-label={title}>
    <div className="na-head"><span className="na-icon">{needsGoogle ? <GoogleMark /> : <KeyRound size={15} />}</span>
      <div><b>{title}</b><p>{line}</p></div></div>
    {(reads.length > 0 || alerts) && <ul className="na-list">
      {reads.map(c => <li key={c} data-ready={have.has(c) || undefined}><GoogleMark /><span>{SERVICE[c.split(":")[0]] ?? c}</span><em>{permissionLabels[c]}</em></li>)}
      {alerts && <li><Bell size={14} /><span>Desktop</span><em>Show alerts</em></li>}
    </ul>}
    {google.pending ? <div className="na-actions"><p className="na-wait"><LoaderCircle size={13} className="animate-spin" />Finish in your browser…</p>
      <button type="button" className="btn-quiet" onClick={() => void google.cancel()}>Cancel</button></div>
    : <div className="na-actions">
      {needsGoogle && <button type="button" className="btn-google" disabled={google.busy} onClick={expired ? reconnect : () => void google.connect({ capabilities: notConnected.length ? notConnected : reads })}>
        <GoogleMark />{expired ? "Reconnect Google" : "Connect Google"}</button>}
      {canAllow && <button type="button" className="btn-primary" disabled={busy} onClick={() => void allow()}>
        {busy && <LoaderCircle size={13} className="animate-spin" />}{reads.length || alerts ? "Allow" : "Carry on"}</button>}
      {other.length > 0 && <button type="button" className="btn-quiet" onClick={onSettings}><Settings2 size={13} />Open its settings</button>}
    </div>}
    {other.length > 0 && <details className="na-more"><summary>What else it needs</summary><ul>{other.map(o => <li key={o}>{o}</li>)}</ul></details>}
    {(error || google.error) && <p className="na-error" role="alert">{error || google.error}</p>}
  </section>;
}
