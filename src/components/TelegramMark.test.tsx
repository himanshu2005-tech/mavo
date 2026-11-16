import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { TelegramStatus } from "../types";
import { TelegramMark } from "./DesktopUpdateCard";
import { OPEN_PAGE, takeOpenPage } from "../lib/openCrew";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), isTauri: () => false }));

const base: TelegramStatus = { connected: false, linked: false, alerts: true, bot_name: null, bot_username: null, chat_name: null, link_url: null };

it("an alert says it also went to Telegram, or offers to set Telegram up", () => {
  const sent = renderToStaticMarkup(<TelegramMark onSetup={() => undefined} initial={{ ...base, connected: true, linked: true, bot_username: "my_mavo_bot" }} />);
  expect(sent).toContain("On Telegram");
  expect(sent).not.toContain("<button");
  const setup = renderToStaticMarkup(<TelegramMark onSetup={() => undefined} initial={base} />);
  expect(setup).toContain("Set up Telegram");
  const off = renderToStaticMarkup(<TelegramMark onSetup={() => undefined} initial={{ ...base, connected: true, linked: true, alerts: false }} />);
  expect(off).toContain("Telegram is off");
});

it("the alert window's request to open a page is taken once, and only while fresh", () => {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) });
  localStorage.setItem(OPEN_PAGE, JSON.stringify({ page: "resources", at: Date.now() }));
  expect(takeOpenPage()).toBe("resources");
  expect(takeOpenPage()).toBeNull();
  localStorage.setItem(OPEN_PAGE, JSON.stringify({ page: "resources", at: Date.now() - 60000 }));
  expect(takeOpenPage()).toBeNull();
});
