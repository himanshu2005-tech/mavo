import { Check, KeyRound, LoaderCircle, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";

export interface SavedLogin { host: string; username_hint: string; saved_at: string }

/** Type it once, here. It goes to Windows' encrypted storage and Mavo fills the sign-in itself;
 * the AI never sees it. */
export function SaveLoginForm({ defaultSite = "", onSaved, compact = false }: { defaultSite?: string; onSaved?: (login: SavedLogin) => void; compact?: boolean }) {
  const [site, setSite] = useState(defaultSite);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(null); setSaved(null);
    try {
      const login = await api.saveLogin(site.trim(), username.trim(), password);
      setPassword(""); setUsername(""); setSaved(login.host); onSaved?.(login);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save this login."); }
    finally { setBusy(false); }
  }

  return <form onSubmit={submit} aria-label="Save a login" className={compact ? "space-y-3" : "space-y-4"}>
    <div className="grid gap-3 sm:grid-cols-3">
      <label className="block text-[12px] font-medium text-zinc-500">Website<input className="form-control" placeholder="placements.haveloc.com" value={site} onChange={e => setSite(e.target.value)} autoComplete="off" disabled={busy} /></label>
      <label className="block text-[12px] font-medium text-zinc-500">Username or email<input className="form-control" value={username} onChange={e => setUsername(e.target.value)} autoComplete="off" disabled={busy} /></label>
      <label className="block text-[12px] font-medium text-zinc-500">Password<input className="form-control" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" disabled={busy} /></label>
    </div>
    <div className="flex flex-wrap items-center gap-3">
      <button className="btn-primary" disabled={busy || !site.trim() || !username.trim() || !password}>{busy ? <LoaderCircle size={13} className="animate-spin" /> : <KeyRound size={13} />}Save login</button>
      {saved && <span role="status" className="flex items-center gap-1.5 text-[13px] text-zinc-300"><Check size={14} />Saved for {saved}.</span>}
    </div>
    <p className="text-[12px] leading-5 text-zinc-500">Encrypted on this computer. Never shown to the AI.</p>
    {error && <p role="alert" className="notice error-notice">{error}</p>}
  </form>;
}

export function SavedLogins() {
  const [logins, setLogins] = useState<SavedLogin[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setLogins(await api.logins()); setError(null); } catch (e) { setError(e instanceof Error ? e.message : "Could not load saved logins."); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function remove(host: string) {
    try { await api.deleteLogin(host); await load(); } catch (e) { setError(e instanceof Error ? e.message : "Could not remove this login."); }
  }
  return <div className="space-y-5">
    {logins.length > 0 && <div className="list-card">{logins.map(login => <div className="list-row" key={login.host}>
      <div className="min-w-0 flex-1"><p className="text-[14px] font-medium text-zinc-50">{login.host}</p><p className="text-[12px] text-zinc-500">{login.username_hint}</p></div>
      <button className="btn-quiet" aria-label={"Remove login for " + login.host} onClick={() => void remove(login.host)}><Trash2 size={14} />Remove</button>
    </div>)}</div>}
    <SaveLoginForm onSaved={() => void load()} />
    {error && <p role="alert" className="notice error-notice">{error}</p>}
  </div>;
}
