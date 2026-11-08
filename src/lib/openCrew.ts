/** The desktop card asks the main window to open a Crew through shared storage (both windows are
 *  the same app, so they share it). A request is only honoured for a short while. */
export const OPEN_CREW = "mavo-open-crew";

export function takeOpenCrew(now = Date.now()): string | null {
  try {
    const raw = localStorage.getItem(OPEN_CREW);
    if (!raw) return null;
    localStorage.removeItem(OPEN_CREW);
    const value = JSON.parse(raw) as { id?: string; at?: number };
    return value.id && value.at && now - value.at < 30000 ? value.id : null;
  } catch { return null; }
}
