export type ThemeChoice = "auto" | "light" | "dark";
/** Mavo has one accent: lime, as a small decoration on black and white. Kept as a type so old saved choices are ignored. */
export type AccentChoice = "lime";

const THEME_KEY = "mavo-theme";

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

/** Light or Dark. "Auto" follows Windows, and keeps following it while the app is open. */
export function applyTheme(choice: ThemeChoice = storedTheme()) {
  const dark = window.matchMedia?.("(prefers-color-scheme: dark)");
  document.documentElement.dataset.theme = choice === "auto" ? (dark?.matches === false ? "light" : "dark") : choice;
}

export function applyAccent(choice: AccentChoice = "lime") {
  document.documentElement.dataset.accent = choice;
}

export function chooseTheme(choice: ThemeChoice) {
  write(THEME_KEY, choice === "auto" ? null : choice);
  applyTheme(choice);
}

/** Called once per window before anything is drawn. */
export function startTheme() {
  applyTheme();
  applyAccent();
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => { if (storedTheme() === "auto") applyTheme("auto"); });
  // The desktop icon window follows a change made in the main window.
  window.addEventListener("storage", (event) => { if (event.key === THEME_KEY) applyTheme(); });
}
