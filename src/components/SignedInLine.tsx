import { Check, CircleHelp, LoaderCircle, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Known = { saved: boolean | null; session?: "in" | "likely" | "out" | "unknown" };

/** After a Crew is added: what Mavo knows about its sign-in to each site. At first only whether a
 * login was saved on this computer (instant, nothing opens). "Check" opens the site once and looks
 * for a sign-out control, which is the only real proof. */
export function SignedInLine({ sites }: { sites: string[] }) {
  const key = sites.join(",");
  const [known, setKnown] = useState<Record<string, Known>>({});
  const [checking, setChecking] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setKnown({});
    for (const host of sites) {
      api.siteSaved(host).then(r => { if (active) setKnown(prev => ({ ...prev, [host]: { saved: r.saved } })); }).catch(() => { if (active) setKnown(prev => ({ ...prev, [host]: { saved: null } })); });
    }
    return () => { active = false; };
  }, [key]);

  async function check(host: string) {
    setChecking(host); setError(null);
    try { const r = await api.probeSite("https://" + host); setKnown(prev => ({ ...prev, [host]: { saved: prev[host]?.saved ?? null, session: r.unknown ? "unknown" : r.wall ? "out" : r.session } })); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not check."); }
    finally { setChecking(null); }
  }
  async function signIn(host: string) {
    setError(null);
    try { await api.browserSignIn("https://" + host); setOpened(host); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open the browser."); }
  }

  if (!sites.length) return null;
  return <div className="space-y-1.5" aria-label="Site sign-in">
    {sites.map(host => {
      const k = known[host];
      if (!k) return null;
      const proven = k.session === "in" || k.session === "likely", out = k.session === "out";
      const text = proven ? <>{k.session === "likely" ? "Looks signed in to " : "Signed in to "}{host}</> : out ? <>Not signed in to {host}</> : k.session === "unknown" ? <>{host}: could not tell if you are signed in</> : k.saved === null ? <>{host}: tap Check to see if you are signed in</> : k.saved ? <>{host}: a sign-in is saved on this computer</> : <>{host}: no sign-in saved yet</>;
      return <p key={host} className="flex flex-wrap items-center gap-2 text-[13px] text-zinc-400">
        {proven ? <Check size={15} className="text-[var(--good)]" /> : out || (k.saved === false && !k.session) ? <TriangleAlert size={15} className="text-[var(--warn)]" /> : <CircleHelp size={15} className="text-zinc-500" />}
        {text}
        {!proven && (opened === host ? <span className="text-[12px] text-zinc-500">Sign in there, then close the window.</span> : <button type="button" className="btn-secondary !py-1" onClick={() => void signIn(host)}>Sign in</button>)}
        <button type="button" className="btn-quiet !py-1" disabled={checking === host} onClick={() => void check(host)}>{checking === host ? <LoaderCircle size={12} className="animate-spin" /> : null}Check</button>
      </p>;
    })}
    {error && <p role="alert" className="notice error-notice">{error}</p>}
  </div>;
}
