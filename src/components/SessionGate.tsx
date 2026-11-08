import { LoaderCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { clearWorkspace } from "../store/app";
import { FirebaseLoginCard } from "./FirebaseLoginCard";

export function SessionGate({ children }: { children: ReactNode }) {
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true, fetching = false;
    async function refresh() {
      if (fetching) return; fetching = true;
      try { const status = await api.firebaseStatus(); if (active) { setSignedIn(status.signed_in); setError(null); if (!status.signed_in) clearWorkspace(); } }
      catch (reason) { if (active) { setSignedIn(false); clearWorkspace(); setError(reason instanceof Error ? reason.message : "Could not verify your login."); } }
      finally { fetching = false; }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    window.addEventListener("mavo-session-changed", refresh);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener("mavo-session-changed", refresh); };
  }, []);
  if (signedIn) return children;
  return <div className="grid min-h-screen place-items-center bg-ink p-8"><div className="w-full max-w-lg"><div className="mb-7 text-center"><span className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-zinc-900 text-lg font-semibold text-white">M</span><h1 className="text-2xl font-semibold tracking-tight text-zinc-200">Welcome to Mavo</h1></div>{signedIn === null ? <p role="status" className="flex items-center justify-center gap-2 text-xs text-zinc-500"><LoaderCircle size={16} className="animate-spin" />Checking your session…</p> : <FirebaseLoginCard />}{error && <p role="alert" className="notice error-notice mt-3">{error}</p>}</div></div>;
}
