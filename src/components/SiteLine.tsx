import { Globe } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";

/** The sites this Crew uses, with one optional way to sign in ahead of time. There is no check
 * here on purpose: your agent looks at the site itself (that is what "Try it first" does, and what
 * every run does) and tells you if it needs you to sign in. A fixed rule cannot tell that as well. */
export function SiteLine({ sites }: { sites: string[] }) {
  const [opened, setOpened] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function signIn(host: string) {
    setError(null);
    try { await api.browserSignIn("https://" + host); setOpened(host); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not open the browser."); }
  }

  if (!sites.length) return null;
  return <div className="space-y-1.5" aria-label="Sites">
    {sites.map(host => <p key={host} className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] text-zinc-400">
      <Globe size={14} className="text-zinc-500" aria-hidden="true" />
      <span className="text-zinc-200">{host}</span>
      <span className="text-zinc-500">Your agent tells you if it needs you to sign in.</span>
      {opened === host
        ? <span className="text-[12px] text-zinc-500">Sign in there, then close the window.</span>
        : <button type="button" className="btn-quiet !py-1" onClick={() => void signIn(host)}>Sign in now</button>}
    </p>)}
    {error && <p role="alert" className="notice error-notice">{error}</p>}
  </div>;
}
