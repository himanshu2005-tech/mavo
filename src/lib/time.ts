/** The backend stores times in UTC, sometimes without a zone marker. A bare "2026-11-03T03:52:00"
 * is UTC, not local time; reading it as local shows the wrong hour. */
export function parseTime(value: string): Date {
  return new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : value + "Z");
}

/** An update shown on the desktop as it happens; older ones (the laptop was closed, or Mavo was
 *  muted) wait in Missed instead of popping up one after another. */
export const FRESH_MS = 15 * 60_000;

export function isFresh(value: string, now = Date.now()): boolean {
  return now - parseTime(value).getTime() <= FRESH_MS;
}

export function localTimestamp(value: string): string {
  return parseTime(value).toLocaleString();
}

export function clockTime(value: string): string {
  return parseTime(value).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
