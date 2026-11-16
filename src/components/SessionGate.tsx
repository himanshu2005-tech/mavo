import { LoaderCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { initialSessionState, SessionStartup, SESSION_POLL_MS, SESSION_STARTUP_RETRY_MS } from '../lib/sessionStartup';

export function SessionGate({ children }: { children: ReactNode }) {
  const [connection, setConnection] = useState(initialSessionState);
  useEffect(() => {
    let active = true, fetching = false;
    let timer: number | undefined;
    const startup = new SessionStartup();
    async function refresh() {
      if (fetching) return; fetching = true;
      window.clearTimeout(timer);
      let delay = SESSION_POLL_MS;
      try {
        const status = await api.firebaseStatus();
        if (active) {
          const next = startup.success(status);
          setConnection(next);
        }
      } catch (reason) {
        if (active) {
          const next = startup.failure(reason);
          setConnection(next);
          if (next.phase === 'checking') delay = SESSION_STARTUP_RETRY_MS;
        }
      } finally {
        fetching = false;
        if (active) timer = window.setTimeout(() => void refresh(), delay);
      }
    }
    const retry = () => { if (!fetching) { startup.retry(); setConnection(initialSessionState()); void refresh(); } };
    void refresh();
    window.addEventListener("mavo-session-changed", refresh);
    window.addEventListener('mavo-session-retry', retry);
    return () => { active = false; window.clearTimeout(timer); window.removeEventListener("mavo-session-changed", refresh); window.removeEventListener('mavo-session-retry', retry); };
  }, []);
  // No account needed: once Mavo's local service answers, the workspace opens. Signing in is
  // only for Mavo Core, optional, in Settings.
  if (connection.phase === 'ready') return children;
  return <div className="grid min-h-screen place-items-center bg-ink p-8"><div className="w-full max-w-lg"><div className="mb-7 text-center"><span className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-zinc-900 text-lg font-semibold text-white">M</span><h1 className="text-2xl font-semibold tracking-tight text-zinc-200">Starting Mavo</h1></div>
    {connection.phase === 'checking' ? <p role="status" className="flex items-center justify-center gap-2 text-xs text-zinc-500"><LoaderCircle size={16} className="animate-spin" />Connecting to your local workspace…</p>
      : <div><p role="alert" className="notice error-notice">{connection.error}</p><button className="btn-secondary mt-3" onClick={() => window.dispatchEvent(new Event('mavo-session-retry'))}>Retry connection</button></div>}
  </div></div>;
}
