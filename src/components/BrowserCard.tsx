import { ExternalLink, Globe, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { SavedLogins } from "./SaveLogin";

const RECENT = "mavo-signin-sites";
function recentSites(): string[] {
  try { return JSON.parse(localStorage.getItem(RECENT) ?? "[]") as string[]; } catch { return []; }
}
function remember(url: string) {
  try { localStorage.setItem(RECENT, JSON.stringify([url, ...recentSites().filter(u => u !== url)].slice(0, 4))); } catch { /* optional */ }
}

/** The single place to sign in. One private window, shared by every Crew on the sites each allows. */
export function BrowserCard() {
  const [state, setState] = useState<{ engine: string; open: boolean; busy: boolean; seconds_left: number } | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recent] = useState(recentSites);

  const load = useCallback(async () => {
    try { setState(await api.browserState()); } catch (e) { setError(e instanceof Error ? e.message : "Could not read the browser state."); }
  }, []);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function open() {
    setBusy(true); setError(null);
    const target = /^https?:\/\//i.test(url.trim()) ? url.trim() : "https://" + url.trim();
    try { await api.browserSignIn(target.replace(/^http:\/\//i, "https://")); remember(target); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open the browser."); }
    finally { setBusy(false); }
  }
  async function done() {
    setBusy(true); setError(null);
    try { await api.browserClose(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not close the browser."); }
    finally { setBusy(false); }
  }

  const engine = state?.engine ?? "your browser";
  return <section className="surface p-6" aria-label="Browser sign-in">
    <div className="flex items-start justify-between gap-4">
      <div className="flex items-start gap-3"><span className="crew-icon"><Globe size={18} /></span><div>
        <h2 className="text-[15px] font-semibold">Browser</h2>
        <p className="mt-1 max-w-xl text-[13px] leading-5 text-zinc-500">Mavo signs in for you. It uses its own private {engine} window, apart from your profile.</p>
      </div></div>
      {state?.open && <span className="badge badge-green">Open</span>}
    </div>
    <h3 className="mb-1 mt-5 text-[13px] font-semibold text-zinc-300">Sign in to a site</h3>
    {state?.open
      ? <div className="mt-3">
          <p role="status" className="text-[13px] leading-5 text-zinc-300">Sign in in the window, including with Google. Then close it or press Done.</p>
          <button className="btn-primary mt-4" disabled={busy} onClick={() => void done()}>{busy && <LoaderCircle size={13} className="animate-spin" />}Done</button>
        </div>
      : <form className="mt-3" onSubmit={e => { e.preventDefault(); if (url.trim()) void open(); }}>
          <label className="block text-[12px] font-medium text-zinc-500">Site to sign in to
            <input className="form-control" aria-label="Site to sign in to" placeholder="placements.haveloc.com" value={url} onChange={e => setUrl(e.target.value)} disabled={busy} />
          </label>
          {recent.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{recent.map(site => <button key={site} type="button" className="btn-quiet !py-1" onClick={() => setUrl(site)}>{site.replace(/^https:\/\//, "")}</button>)}</div>}
          <button className="btn-secondary mt-4" disabled={busy || !url.trim() || state?.busy} type="submit">{busy ? <LoaderCircle size={13} className="animate-spin" /> : <ExternalLink size={13} />}Open browser to sign in</button>
          {state?.busy && <p className="mt-3 text-[12px] text-zinc-500">A Crew is using the browser. Try again shortly.</p>}
        </form>}
    {error && <p role="alert" className="notice error-notice mt-4">{error}</p>}
    <details className="setup-more mt-6"><summary>Use a saved password instead</summary><div className="mt-4"><SavedLogins /></div></details>
  </section>;
}
