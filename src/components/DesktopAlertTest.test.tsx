import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";

const native = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: native.invoke }));
import { DesktopAlertTest, openDesktopAlertTest } from "./DesktopAlertTest";

beforeEach(() => native.invoke.mockReset());

it("offers a concise local display test while preserving the settings appearance", () => {
  const html = renderToStaticMarkup(<DesktopAlertTest />);
  expect(html).toContain("Test desktop alert");
  expect(html).toContain("Test alert");
  expect(html).toContain("Crew alerts still respect mute");
});

it("shows a local preview without backend calls or changing mute", async () => {
  native.invoke.mockResolvedValueOnce(null).mockResolvedValueOnce(undefined);
  await openDesktopAlertTest();
  expect(native.invoke).toHaveBeenNthCalledWith(1, "pending_reminder");
  expect(native.invoke.mock.calls[1][0]).toBe("show_reminder_overlay");
  expect(native.invoke.mock.calls[1][1].reminder.preview).toBe(true);
  expect(native.invoke).toHaveBeenCalledTimes(2);
});

it("reopens an unread real update instead of overwriting it with a preview", async () => {
  const reminder = { id: "saved", crew_name: "Inbox", message: "Saved update" };
  native.invoke.mockResolvedValueOnce(reminder).mockResolvedValueOnce(undefined);
  await openDesktopAlertTest();
  expect(native.invoke).toHaveBeenLastCalledWith("show_reminder_overlay", { reminder });
});

it("surfaces native show failures without pretending delivery succeeded", async () => {
  native.invoke.mockResolvedValueOnce(null).mockRejectedValueOnce(Error("window missing"));
  await expect(openDesktopAlertTest()).rejects.toThrow("window missing");
});
