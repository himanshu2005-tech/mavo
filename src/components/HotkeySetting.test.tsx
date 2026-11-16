import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { CrewInstance } from "../types";
import { HotkeySetting, comboOf } from "./HotkeySetting";

const press = (key: string, code: string, mods: Partial<Record<"ctrlKey" | "altKey" | "shiftKey" | "metaKey", boolean>> = {}) =>
  ({ key, code, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...mods });

it("turns a key press into a shortcut, waiting while only modifiers are held", () => {
  expect(comboOf(press("F9", "F9", { metaKey: true }))).toBe("Win+F9");
  expect(comboOf(press("®", "KeyR", { ctrlKey: true, altKey: true }))).toBe("Ctrl+Alt+R");  // the key, not what it types
  expect(comboOf(press("Control", "ControlLeft", { ctrlKey: true }))).toBeNull();
  expect(comboOf(press(" ", "Space", { ctrlKey: true, shiftKey: true }))).toBe("Ctrl+Shift+Space");
});

it("shows the keys as keycaps, with Change and Remove, and says when another app holds them", () => {
  const crew = { id: "c", name: "Credits", hotkey: "Win+F9", hotkey_problem: "Win+F9 is already used by Windows or another app. Pick another." } as CrewInstance;
  const html = renderToStaticMarkup(<HotkeySetting crew={crew} />);
  expect(html).toContain("<span>Win</span><span>F9</span>");
  expect(html).toContain(">Change<");
  expect(html).toContain('aria-label="Remove the shortcut"');
  expect(html).toContain("already used by Windows or another app");
  expect(renderToStaticMarkup(<HotkeySetting crew={{ ...crew, hotkey: null, hotkey_problem: null }} />)).toContain(">Set<");
});
