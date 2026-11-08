import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { BellRing, Volume2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Reminder } from "../types";

export function ReminderOverlay() {
  const [reminder, setReminder] = useState<Reminder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [voice, setVoice] = useState<"ready" | "speaking" | "unavailable">("ready");
  useEffect(() => {
    document.documentElement.classList.add("overlay-root");
    let active = true;
    function show(value: Reminder | null) {
      if (!active) return;
      setReminder(value); setError(null);
      if (value && value.kind !== "crew_update") speak(value.message, setVoice);
    }
    const unlisten = listen<Reminder>("reminder-updated", (event) => show(event.payload));
    void unlisten.then(() => invoke<Reminder | null>("pending_reminder")).then(show)
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Reminder unavailable"); });
    return () => { active = false; void unlisten.then((cleanup) => cleanup()); document.documentElement.classList.remove("overlay-root"); window.speechSynthesis?.cancel(); };
  }, []);

  async function dismiss() {
    if (!reminder) return;
    try {
      if (!reminder.preview) await api.dismissReminder(reminder.id);
      window.speechSynthesis?.cancel();
      await invoke("close_reminder_overlay");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not dismiss reminder.");
    }
  }

  return <div className="h-screen bg-ink p-3 text-zinc-200"><div className="relative flex h-full flex-col overflow-hidden rounded-2xl border border-line bg-panel p-5 shadow-2xl">

    <div className="flex items-center gap-3"><div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-mint/15 text-mint"><BellRing size={19} /></div><div className="min-w-0"><div className="text-[11px] text-zinc-500">{reminder?.preview ? "Reminder preview" : reminder?.kind === "crew_update" ? "Crew update" : "Mavo reminder"}</div><h1 className="mt-0.5 truncate text-sm font-medium text-white">{reminder?.crew_name ?? "Loading…"}</h1></div></div>
    <p className="message-content mt-4 max-h-24 overflow-y-auto text-sm leading-5 text-zinc-300">{reminder?.message ?? error ?? "Preparing your reminder…"}</p>
    {error && reminder && <p className="mt-1 text-[10px] text-rose-300">{error}</p>}
    {voice === "unavailable" && <p className="mt-2 text-[10px] text-amber-200">Voice could not start. Try Read aloud or check Windows voices.</p>}
    <div className="mt-auto flex items-center justify-between"><button disabled={!reminder} onClick={() => reminder && speak(reminder.message, setVoice)} className="inline-flex items-center gap-1.5 rounded-lg px-2 py-2 text-[10px] text-zinc-500 hover:bg-white/5 hover:text-zinc-200"><Volume2 size={13} /> {voice === "speaking" ? "Reading aloud…" : "Read aloud"}</button><button disabled={!reminder} onClick={() => void dismiss()} className="btn-primary">Dismiss</button></div>
  </div></div>;
}

export function speak(message: string, onState: (state: "ready" | "speaking" | "unavailable") => void) {
  if (!("speechSynthesis" in window)) { onState("unavailable"); return; }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(message);
  utterance.rate = 0.95;
  utterance.onstart = () => onState("speaking");
  utterance.onend = () => onState("ready");
  utterance.onerror = (event) => { if (event.error !== "canceled" && event.error !== "interrupted") onState("unavailable"); };
  window.speechSynthesis.speak(utterance);
}
