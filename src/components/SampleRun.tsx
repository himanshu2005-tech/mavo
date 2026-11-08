import { AlertCircle, Check, LoaderCircle, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { Report } from "./Report";
import { SaveLoginForm } from "./SaveLogin";

export interface TrialResult {
  supported: boolean; message?: string; ok?: boolean; verdict?: string; headline?: string; detail?: string;
  url?: string; page_characters?: number; waited_for_page_seconds?: number | null;
  sign_in_required?: { kind: "login" | "captcha"; host: string; url?: string; login_url?: string } | null; result?: string;
  signing_in?: boolean;
  steps?: { label: string; seconds: number }[]; trail?: string[]; model_calls?: number; seconds?: number;
}

/** A sample run happens in the background; this is how far it has got. */
export interface TrialStatus { status: "none" | "running" | "done"; stage?: string; seconds?: number; result?: TrialResult | null; error?: string | null }

function OpenSignIn({ site }: { site: string }) {
  const [state, setState] = useState<"idle" | "open" | "error">("idle");
  const [message, setMessage] = useState("");
  async function open() {
    try { await api.browserSignIn("https://" + site); setState("open"); }
    catch (e) { setMessage(e instanceof Error ? e.message : "Could not open the browser."); setState("error"); }
  }
  return <div className="space-y-2">
    <button type="button" className="btn-primary" onClick={() => void open()} disabled={state === "open"}>Open browser to sign in</button>
    <p className="text-[12px] text-zinc-500">{state === "open" ? "Sign in, including with Google, then close the window and run this again." : state === "error" ? message : "Works with Google, GitHub and other single sign-on."}</p>
  </div>;
}

/** The sign-in window is already open on the login page. Say so, wait, and carry on by itself. */
function SigningIn({ wall, again, onDone }: { wall: NonNullable<TrialResult["sign_in_required"]>; again: boolean; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const finished = useRef(false);

  useEffect(() => {
    const timer = window.setInterval(async () => {
      try { const state = await api.browserState(); if (!state.open && !finished.current) { finished.current = true; onDone(); } }
      catch { /* keep waiting */ }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [onDone]);

  async function signedIn() {
    setBusy(true);
    try { await api.browserClose(); } catch { /* the poll below notices the window anyway */ }
  }

  return <div className="signin-panel" role="status" aria-label="Please sign in">
    <div className="flex items-center gap-3">
      <span className="signin-dot" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <h4 className="text-[15px] font-semibold text-zinc-50">{wall.kind === "captcha" ? "Please complete the check" : "Please sign in"}</h4>
        <p className="mt-0.5 text-[13px] text-zinc-400">{wall.host} is open in Mavo's browser{again ? ". Your agent still found the sign-in page" : ""}. {wall.kind === "captcha" ? "Finish the check there" : "Sign in there"}, and the test continues on its own.</p>
      </div>
    </div>
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <button type="button" className="btn-primary" disabled={busy} onClick={() => void signedIn()}>{busy && <LoaderCircle size={13} className="animate-spin" />}I've signed in</button>
      <span className="text-[12px] text-zinc-500">Or just close the window.</span>
    </div>
  </div>;
}

const wait = (ms: number) => new Promise<void>(resolve => window.setTimeout(resolve, ms));

/** Do what the Crew would do, once, for real. Nothing is saved. Your agent looks at the site and
 * decides; the app only reports what it did. */
export function SampleRun({ crewId }: { crewId: string }) {
  const [state, setState] = useState<"idle" | "running" | "signin" | "done">("idle");
  const [stage, setStage] = useState("Opening the site");
  const [result, setResult] = useState<TrialResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempts, setAttempts] = useState(0);
  const mounted = useRef(true);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  // Follow a run that is already going (the person left this screen and came back).
  useEffect(() => {
    void api.trialStatus(crewId).then(s => { if (mounted.current && s.status === "running") { setState("running"); void follow(0); } }).catch(() => undefined);
  }, [crewId]);

  async function follow(count: number) {
    let misses = 0;
    while (mounted.current) {
      await wait(1500);
      try {
        const s = await api.trialStatus(crewId);
        misses = 0;
        if (s.stage) setStage(s.stage);
        if (s.status === "running") continue;
        if (s.error) setError(s.error);
        const next = s.result ?? null;
        setResult(next);
        // The agent looked at the site and said it needs you to sign in: the browser is already
        // open on the login page, so wait for the person.
        setState(next?.verdict === "sign_in" && next.signing_in && next.sign_in_required ? "signin" : "done");
        setAttempts(count);
        return;
      } catch (e) {
        if (++misses >= 5) { setError(e instanceof Error ? e.message : "Lost contact with the test."); setState("done"); return; }
      }
    }
  }

  async function run(count = 0) {
    setState("running"); setError(null); setResult(null); setAttempts(count); setStage("Opening the site");
    try { await api.startTrial(crewId); await follow(count); }
    catch (e) { setError(e instanceof Error ? e.message : "The sample run could not start."); setState("done"); }
  }

  const wall = result?.sign_in_required;
  return <div className="space-y-4" aria-label="Sample run">
    <div className="flex flex-wrap items-center gap-3">
      <button type="button" className="btn-secondary" disabled={state === "running" || state === "signin"} onClick={() => void run()}>
        {state === "running" ? <LoaderCircle size={13} className="animate-spin" /> : <Play size={12} />}{state === "done" ? "Run again" : "Try it first"}
      </button>
      {state === "idle" && <span className="text-[12px] text-zinc-500">Watch your agent do it once. Nothing is saved.</span>}
      {state === "running" && <span key={stage} role="status" className="stages-text text-[13px]">{stage}…</span>}
    </div>
    {error && <p role="alert" className="notice error-notice">{error}</p>}
    {state === "signin" && wall && <SigningIn wall={wall} again={attempts > 0} onDone={() => void run(attempts + 1)} />}
    {result && !result.supported && <p className="text-[13px] text-zinc-500">{result.message}</p>}
    {state === "done" && result?.supported && <section className="trial" aria-label="Sample run result">
      <div className="flex items-center gap-2.5">
        {result.ok ? <Check size={17} className="text-[var(--good)]" /> : <AlertCircle size={17} className="text-[var(--warn)]" />}
        <h4 className="text-[14px] font-semibold text-zinc-50">{result.headline}</h4>
        <span className="ml-auto text-[12px] tabular-nums text-zinc-500">{result.seconds}s</span>
      </div>
      {result.result && <div className="mt-4"><Report text={result.result} /></div>}
      {result.ok && !!result.trail?.length && <ol className="trail" aria-label="What the agent did">{result.trail.map(step => <li key={step}>{step}</li>)}</ol>}
      {result.detail && <p className="mt-3 text-[13px] leading-6 text-zinc-400">{result.detail}</p>}
      {wall && <div className="mt-4 space-y-3">
        <p className="text-[13px] text-zinc-300">{wall.kind === "captcha" ? `${wall.host} wants a human check. Complete it once in the browser window.` : `${wall.host} needs you to sign in.`}</p>
        <OpenSignIn site={wall.host} />
        {wall.kind === "login" && <details><summary className="text-[12px] text-zinc-500">Site uses a password instead?</summary><div className="mt-3"><SaveLoginForm compact defaultSite={wall.host} /></div></details>}
      </div>}
      {!!result.steps?.length && <details className="trial-more"><summary>Timing</summary><dl className="trial-steps">{result.steps.map(s => <div key={s.label}><dt>{s.label}</dt><dd>{s.seconds}s</dd></div>)}
        {result.page_characters ? <div><dt>Page read</dt><dd>{result.page_characters.toLocaleString()} characters</dd></div> : null}
        {result.model_calls !== undefined ? <div><dt>Model calls</dt><dd>{result.model_calls}</dd></div> : null}</dl></details>}
    </section>}
  </div>;
}
