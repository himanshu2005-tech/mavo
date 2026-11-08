import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef } from "react";
import { api } from "../lib/api";
import { MUTE_EVENT, isMuted } from "../lib/mute";
import { isFresh } from "../lib/time";
import type { Reminder } from "../types";

/** Brings due updates to the desktop icon as they happen, unless the person has muted it: then
 *  they wait. One that is no longer fresh (the laptop was closed when it came) does not pop up:
 *  it goes to Missed, so opening the laptop does not play back the whole day one by one. */
export function ReminderWatcher({ onMissed }: { onMissed?: (missed: Reminder[]) => void }) {
  const shown = useRef(new Set<string>());
  const report = useRef(onMissed);
  report.current = onMissed;
  useEffect(() => {
    let active = true;
    let checking = false;
    async function check() {
      if (checking) return;
      checking = true;
      try {
        const signedIn = (await api.firebaseStatus()).signed_in;
        if (!active) return;
        if (!signedIn) { await invoke("close_reminder_overlay"); return; }
        const visible = await invoke<{id:string;preview?:boolean}|null>("pending_reminder");
        const reminders = await api.dueReminders();
        if (!active) return;
        const now = Date.now();
        report.current?.(reminders.filter(r => !isFresh(r.scheduled_for, now) && r.id !== visible?.id));
        if (visible) {
          if (!visible.preview && !reminders.some(r=>r.id===visible.id)) await invoke("close_reminder_overlay");
          return;
        }
        // Muted: leave them pending. They show as soon as the mute ends (or is lifted).
        if (isMuted()) return;
        const next = reminders.find((reminder) => isFresh(reminder.scheduled_for, now) && !shown.current.has(reminder.id));
        if (!active || !next) return;
        await invoke("show_reminder_overlay", { reminder: next });
        shown.current.add(next.id);
      } catch (error) {
        if (active) console.warn("Mavo reminder check failed", error);
      } finally {
        checking = false;
      }
    }
    void check();
    const timer = window.setInterval(() => void check(), 15_000);
    // The moment mute is lifted or runs out, bring what waited.
    let wasMuted = isMuted();
    const lifted = () => { const now = isMuted(); if (wasMuted && !now) void check(); wasMuted = now; };
    window.addEventListener(MUTE_EVENT, lifted);
    window.addEventListener("storage", lifted);
    const wake = window.setInterval(lifted, 2_000); // a local look at the clock, no network
    return () => { active = false; window.clearInterval(timer); window.clearInterval(wake); window.removeEventListener(MUTE_EVENT, lifted); window.removeEventListener("storage", lifted); };
  }, []);
  return null;
}
