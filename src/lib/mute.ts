import { useCallback, useEffect, useState } from "react";

/* Mute silences desktop pop-ups and voice for a while. Crew keep working and nothing is lost: the
   updates wait, and appear when the time is up. The deadline lives in localStorage, which every
   Mavo window shares, so muting from the desktop icon is seen by all of them. */

const KEY = "mavo-muted-until";
export const MUTE_EVENT = "mavo-mute-changed";
export const MUTE_CHOICES = [{ minutes: 5, label: "5 min" }, { minutes: 15, label: "15 min" }, { minutes: 60, label: "1 hour" }];

export function mutedUntil(now = Date.now()): number | null {
  try { const value = Number(localStorage.getItem(KEY)); return value > now ? value : null; } catch { return null; }
}

export const isMuted = (now = Date.now()) => mutedUntil(now) !== null;

function announce() { window.dispatchEvent(new Event(MUTE_EVENT)); }

export function muteFor(minutes: number, now = Date.now()) {
  try { localStorage.setItem(KEY, String(now + minutes * 60_000)); } catch { /* muting just will not last */ }
  announce();
}

export function unmute() {
  try { localStorage.removeItem(KEY); } catch { /* nothing to undo */ }
  announce();
}

export function secondsLeft(now = Date.now()): number {
  const until = mutedUntil(now);
  return until === null ? 0 : Math.ceil((until - now) / 1000);
}

export const minutesLeft = (now = Date.now()) => Math.ceil(secondsLeft(now) / 60);

/** 272 seconds becomes "4:32". */
export function countdown(seconds: number): string {
  const s = Math.max(0, seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The mute state for a window: seconds left (0 = not muted), ticking once a second while muted. */
export function useMute() {
  const [seconds, setSeconds] = useState(() => secondsLeft());
  useEffect(() => {
    const sync = () => setSeconds(secondsLeft());
    window.addEventListener(MUTE_EVENT, sync);
    window.addEventListener("storage", sync);
    const timer = window.setInterval(sync, 1000);
    return () => { window.removeEventListener(MUTE_EVENT, sync); window.removeEventListener("storage", sync); window.clearInterval(timer); };
  }, []);
  const mute = useCallback((minutes: number) => { muteFor(minutes); setSeconds(secondsLeft()); }, []);
  const stop = useCallback(() => { unmute(); setSeconds(0); }, []);
  return { seconds, muted: seconds > 0, mute, unmute: stop };
}
