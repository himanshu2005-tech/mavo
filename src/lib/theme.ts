export type ThemeChoice = "auto" | "light" | "dark";
export type AccentChoice = "graphite" | "coral" | "emerald" | "violet" | "sky";

export const ACCENTS: { id: AccentChoice; label: string; swatch: string }[] = [
  { id: "graphite", label: "Graphite", swatch: "#2b2a27" },
  { id: "coral", label: "Coral", swatch: "#e5592f" },
  { id: "emerald", label: "Emerald", swatch: "#12a373" },
  { id: "violet", label: "Violet", swatch: "#7352e8" },
  { id: "sky", label: "Sky", swatch: "#2b87ea" },
];

const THEME_KEY = "mavo-theme";
const ACCENT_KEY = "mavo-accent";

function read(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function write(key: string, value: string | null) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* the choice just will not be remembered */ }
}

export function storedTheme(): ThemeChoice {
  const value = read(THEME_KEY);
  return value === "light" || value === "dark" ? value : "auto";
}

export function storedAccent(): AccentChoice {
  const value = read(ACCENT_KEY);
  return ACCENTS.some(a => a.id === value) ? (value as AccentChoice) : "graphite";
}

/** Light or Dark. "Auto" follows Windows, and keeps following it while the app is open. */
export function applyTheme(choice: ThemeChoice = storedTheme()) {
  const dark = window.matchMedia?.("(prefers-color-scheme: dark)");
  document.documentElement.dataset.theme = choice === "auto" ? (dark?.matches === false ? "light" : "dark") : choice;
}

export function applyAccent(choice: AccentChoice = storedAccent()) {
  document.documentElement.dataset.accent = choice;
}

export function chooseTheme(choice: ThemeChoice) {
  write(THEME_KEY, choice === "auto" ? null : choice);
  applyTheme(choice);
}

export function chooseAccent(choice: AccentChoice) {
  write(ACCENT_KEY, choice === "graphite" ? null : choice);
  applyAccent(choice);
}

/** Called once per window before anything is drawn. */
export function startTheme() {
  applyTheme();
  applyAccent();
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => { if (storedTheme() === "auto") applyTheme("auto"); });
  // The desktop icon window follows a change made in the main window.
  window.addEventListener("storage", (event) => { if (event.key === THEME_KEY) applyTheme(); if (event.key === ACCENT_KEY) applyAccent(); });
}
