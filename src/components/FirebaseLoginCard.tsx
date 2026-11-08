import { invoke } from "@tauri-apps/api/core";
import { LoaderCircle, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { clearWorkspace } from "../store/app";
import type { FirebaseStatus } from "../types";

export function FirebaseLoginCard() {
  const [status, setStatus] = useState<FirebaseStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active = true; void api.firebaseStatus().then(s => { if (active) setStatus(s); }).catch((e: Error) => { if (active) setError(e.message); }); return () => { active = false; }; }, []);
  useEffect(() => { if (status?.signed_in) window.dispatchEvent(new Event("mavo-session-changed")); }, [status?.signed_in]);
  const pending = status?.phase === "pending" || status?.phase === "exchanging";
  useEffect(() => {
    if (!pending) return;
    let active = true, fetching = false;
    const timer = window.setInterval(() => { if (fetching) return; fetching = true; void api.firebaseStatus().then(s => { if (active) setStatus(s); }).catch((e: Error) => { if (active) setError(e.message); }).finally(() => { fetching = false; }); }, 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, [pending]);
  async function login() {
    setBusy(true); setError(null);
    try { const result = await api.firebaseStart(); setStatus(await api.firebaseStatus()); await invoke("open_google_url", { url: result.authorization_url }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not open sign-in."); }
    finally { setBusy(false); }
  }
  async function logout() {
    setBusy(true); setError(null);
    try {
      await api.firebaseSignOut();
      clearWorkspace();
      await invoke("close_reminder_overlay").catch(() => console.warn("The reminder window could not close after logout."));
      setStatus(previous => previous ? {...previous, signed_in: false, email: null, name: null} : previous);
      window.dispatchEvent(new Event("mavo-session-changed"));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not log out."); }
    finally { setBusy(false); }
  }
  return <section className="surface p-6" aria-label="Mavo account">
    <div className="flex items-center gap-3"><span className="crew-icon"><UserRound size={18} /></span><div><h2 className="text-sm font-semibold text-zinc-200">Mavo account</h2>{status?.signed_in && <p className="mt-1 text-xs text-zinc-500">{status.email}</p>}</div></div>
    {status && !status.configured && <p className="mt-3 text-xs text-zinc-500">Sign-in is currently unavailable.</p>}
    {error || status?.error ? <p role="alert" className="notice error-notice mt-3">{error || status?.error}</p> : null}
    <div className="mt-4 flex gap-3">{status?.signed_in ? <button className="btn-secondary" disabled={busy} onClick={() => void logout()}>{busy && <LoaderCircle size={13} className="animate-spin" />}Logout</button> : <button className="btn-primary" disabled={!status?.configured || busy || pending} onClick={() => void login()}>{busy || pending ? <LoaderCircle size={14} className="animate-spin" /> : <UserRound size={14} />}{pending ? "Waiting for Google…" : "Sign in"}</button>}{pending && <button className="btn-secondary" onClick={() => void api.firebaseCancel().then(setStatus).catch((e: Error) => setError(e.message))}>Cancel</button>}</div>
  </section>;
}
