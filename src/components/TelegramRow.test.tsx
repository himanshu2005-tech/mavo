import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { CrewInstance, TelegramStatus } from "../types";
import { TelegramRow } from "./CrewSetupCard";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), isTauri: () => false }));

const linked: TelegramStatus = { connected: true, linked: true, alerts: false, bot_name: null, bot_username: "my_mavo_bot", chat_name: null, link_url: null };
const crew = (telegram: boolean) => ({ id: "c", configuration_plan: { draft: { requested_telegram: telegram } } }) as unknown as CrewInstance;

it("a Send to Telegram switch sits beside desktop alerts", () => {
  const on = renderToStaticMarkup(<TelegramRow crew={crew(true)} alerts initial={linked} />);
  expect(on).toContain("Send to Telegram");
  expect(on).toContain('aria-checked="true"');
  expect(on).toContain("Its alerts go to your Telegram too.");
  const needsAlerts = renderToStaticMarkup(<TelegramRow crew={crew(true)} alerts={false} initial={linked} />);
  expect(needsAlerts).toContain("Turn on desktop alerts first");
  expect(needsAlerts).toContain('aria-checked="false"');
  const notLinked = renderToStaticMarkup(<TelegramRow crew={crew(false)} alerts initial={{ ...linked, linked: false }} />);
  expect(notLinked).toContain("not linked yet");
  expect(notLinked).toContain(">Set up</button>");
});
