import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef } from "react";
import { api } from "../lib/api";
import { MUTE_EVENT, isMuted } from "../lib/mute";
import { ALERT_POLL_MS, ReminderDelivery } from "../lib/reminderDelivery";
import type { Reminder } from "../types";

/** Brings due updates to the desktop icon as they happen, unless the person has muted it: then
 *  they wait. One that is no longer fresh (the laptop was closed when it came) does not pop up:
 *  it goes to Missed, so opening the laptop does not play back the whole day one by one. */
export function ReminderWatcher({ onMissed, onError }: { onMissed?: (missed: Reminder[]) => void; onError?: (error: string | null) => void }) {
  const report = useRef(onMissed);
  const failure = useRef(onError);
  report.current = onMissed;
  failure.current = onError;
  useEffect(() => {
    let active = true;
    const delivery = new ReminderDelivery({
      signedIn: async () => true,  // no account needed: alerts come whether or not you signed in to Mavo Core
      pending: () => invoke("pending_reminder"),
      due: api.dueReminders,
      show: reminder => invoke("show_reminder_overlay", { reminder }),
      close: () => invoke("close_reminder_overlay"),
      muted: isMuted,
      // Never over one being read in full, one that waits for an answer while it is open, or a
      // reply being typed: a routine update replaced a question nine seconds after it came.
      canReplace: () => !document.querySelector('.toast-stage[data-reader="true"]') && !document.querySelector('.toast[data-answerable="true"]') && !Array.from(document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(".toast-stage input, .toast-stage textarea"))
        .some(input => input === document.activeElement || input.value.trim().length > 0),
      missed: reminders => report.current?.(reminders),
    });
    const check = () => delivery.check().then(() => { if (active) failure.current?.(null); }).catch(error => {
      if (active) {
        failure.current?.("Desktop alerts are reconnecting. Unread updates remain saved.");
        console.warn("Mavo reminder check failed", error);
      }
    });
    void check();
    const timer = window.setInterval(() => void check(), ALERT_POLL_MS);
    // The moment mute is lifted or runs out, bring what waited.
    let wasMuted = isMuted();
    const lifted = () => { const now = isMuted(); if (wasMuted && !now) void check(); wasMuted = now; };
    window.addEventListener(MUTE_EVENT, lifted);
    window.addEventListener("storage", lifted);
    const wake = window.setInterval(lifted, 2_000); // a local look at the clock, no network
    const wakeUp = () => { void check(); };
    window.addEventListener("focus", wakeUp);
    document.addEventListener("visibilitychange", wakeUp);
    return () => { active = false; delivery.stop(); window.clearInterval(timer); window.clearInterval(wake); window.removeEventListener(MUTE_EVENT, lifted); window.removeEventListener("storage", lifted); window.removeEventListener("focus", wakeUp); document.removeEventListener("visibilitychange", wakeUp); };
  }, []);
  return null;
}
