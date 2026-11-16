import { invoke, isTauri } from "@tauri-apps/api/core";
import { Check, ExternalLink, LoaderCircle, Send } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { useAppStore } from "../store/app";
import type { TelegramStatus } from "../types";
import { Switch } from "./CrewSetupCard";

function open(url: string) {
  if (isTauri()) void invoke("open_report_url", { url }).catch(() => window.open(url, "_blank"));
  else window.open(url, "_blank", "noopener");
}

function Step({ n, done, title, where, children }: { n: number; done: boolean; title: string; where: "On your phone" | "Here" | "Both"; children?: ReactNode }) {
  return <li className="tg-step" data-done={done || undefined}>
    <span className="tg-n" aria-hidden="true">{done ? <Check size={12} strokeWidth={3} /> : n}</span>
    <div className="min-w-0 flex-1"><p className="tg-title">{title}<span className="tg-where" data-where={where}>{where === "Both" ? "Phone and here" : where}</span></p>{children}</div>
  </li>;
}

/** Your own Telegram bot, step by step: make it with BotFather, paste its token, press Start in
 *  Telegram. Then your alerts come there too, and "@Agent message" talks to that agent. */
export function TelegramCard() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const crews = useAppStore(s => s.crew);
  const example = crews[0]?.name ?? "Agent name";

  useEffect(() => { void api.telegram().then(setStatus).catch(() => undefined); }, []);
  // Waiting for Start in Telegram: look now and then until it is linked.
  useEffect(() => {
    if (!status?.connected || status.linked) return;
    const t = window.setInterval(() => void api.telegram().then(setStatus).catch(() => undefined), 3000);
    return () => window.clearInterval(t);
  }, [status?.connected, status?.linked]);

  async function run<T>(work: () => Promise<T>, then?: (v: T) => void) {
    setBusy(true); setError(null); setNote(null);
    try { const v = await work(); then?.(v); }
    catch (e) { setError(e instanceof Error ? e.message : "Something went wrong."); }
    finally { setBusy(false); }
  }

  const connected = !!status?.connected, linked = !!status?.linked;
  return <section className="surface tg-card" aria-label="Telegram">
    <header className="tg-head">
      <span className="tg-logo" aria-hidden="true"><Send size={15} /></span>
      <div className="min-w-0 flex-1"><h3>Telegram</h3>
        <p>{linked ? `Linked to ${status?.chat_name ?? "you"} through @${status?.bot_username}` : "Get your alerts on your phone, and talk to your agents from there."}</p></div>
      {connected && <button type="button" className="btn-quiet" disabled={busy} onClick={() => void run(api.disconnectTelegram, () => setStatus({ connected: false, linked: false, alerts: true, bot_name: null, bot_username: null, chat_name: null, link_url: null }))}>Disconnect</button>}
    </header>

    {!linked && <ol className="tg-steps">
      <Step n={1} done={connected} title="Make your own bot" where="On your phone">
        {!connected && <p className="tg-hint">In Telegram, open <button type="button" className="ask-link" onClick={() => open("https://t.me/BotFather")}>@BotFather<ExternalLink size={12} /></button> and send <code>/newbot</code>. Give it any name (like “My Mavo”), then a username ending in <code>bot</code> (like <code>my_mavo_bot</code>). BotFather replies with a long token: copy it.</p>}
      </Step>
      <Step n={2} done={connected} title="Paste the token" where="Here">
        {!connected && <form className="tg-token" onSubmit={e => { e.preventDefault(); void run(() => api.connectTelegram(token.trim()), v => { setStatus(v); setToken(""); }); }}>
          <input type="password" autoComplete="off" spellCheck={false} aria-label="Bot token" placeholder="123456789:AAE…" value={token} onChange={e => setToken(e.target.value)} />
          <button type="submit" className="btn-primary" disabled={busy || !token.trim()}>{busy && <LoaderCircle size={13} className="animate-spin" />}Connect</button>
        </form>}
        {!connected && <p className="tg-hint">It is kept encrypted on this computer and never shown again. Anyone with it can use your bot, so share it with no one.</p>}
      </Step>
      <Step n={3} done={linked} title="Send this code to your bot" where="On your phone">
        {connected && (status?.code_expired
          ? <><p className="tg-hint">The code expired.</p><button type="button" className="btn-secondary mt-2" disabled={busy} onClick={() => void run(api.newTelegramCode, setStatus)}>New code</button></>
          : <>
            <p className="tg-hint">In Telegram, search for <b>@{status?.bot_username}</b>, open it and press <b>Start</b>. Then send it this code:</p>
            {status?.link_code && <p className="tg-code" aria-label="Your code">{status.link_code.slice(0, 3)} {status.link_code.slice(3)}</p>}
            <p className="tg-hint">It works for 30 minutes, once. Only the chat that sends it is linked: messages from anyone else are ignored.</p>
            {status?.link_url && <p className="tg-hint">Telegram on this computer? <button type="button" className="ask-link" onClick={() => open(status.link_url!)}>Open @{status.bot_username} here<ExternalLink size={12} /></button> and press Start: no code needed.</p>}
          </>)}
      </Step>
      <Step n={4} done={linked} title="Wait for the reply" where="Both">
        {connected && <><p className="tg-hint">Your bot answers “Linked”, and this card turns into your Telegram settings.</p>
          <p className="tg-wait"><LoaderCircle size={12} className="animate-spin" />Waiting for your code…</p></>}
      </Step>
    </ol>}

    {linked && <div className="tg-linked">
      <div className="tg-row"><div><p className="tg-title">Send all my alerts to Telegram</p><p className="tg-hint">{status?.alerts ? "Every agent: everything that shows on your desktop, there too." : "Only the agents you asked to send there (say “send it on Telegram too” when you make one)."} A question comes with its answers as buttons.</p></div>
        <Switch on={!!status?.alerts} onChange={on => void run(() => api.telegramAlerts(on), setStatus)} /></div>
      <div className="tg-how"><p className="tg-title">Talk to an agent</p>
        <p className="tg-hint">Start your message with its name: <code>@{example} what's new?</code> It answers there, as in its room here. Send <code>/agents</code> to list them. To answer a question, tap a button or reply to it.</p></div>
      <button type="button" className="btn-quiet" disabled={busy} onClick={() => void run(api.testTelegram, () => setNote("Sent. Check Telegram."))}>Send a test message</button>
    </div>}
    {note && <p className="tg-hint" role="status">{note}</p>}
    {error && <p className="na-error" role="alert">{error}</p>}
  </section>;
}
