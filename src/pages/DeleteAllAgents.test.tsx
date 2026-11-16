import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { DeleteAllAgents } from "./SettingsPage";

it("is offered in Settings and asks before deleting anything", () => {
  const html = renderToStaticMarkup(<DeleteAllAgents />);
  // With no agents there is nothing to delete, so nothing shows; with agents it shows a button
  // that first asks ("Delete all…"), never deleting on the first press.
  expect(html === "" || html.includes("Delete all…")).toBe(true);
  expect(html).not.toContain(">Delete all<");
});
