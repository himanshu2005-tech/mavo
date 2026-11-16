import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { Understood } from "./Understood";

it("shows Mavo Core's brief first, short, with a way to change it", () => {
  const brief = "Watch new mail for meeting requests. Meetings are 30 minutes, weekdays 9 AM to 6 PM IST, with 15 minutes free before and after. Book the earliest offered free time; if none is free, offer up to three. Stop after 3 rounds and tell you.";
  const html = renderToStaticMarkup(<Understood text={brief} onChange={() => {}} />);
  expect(html).not.toContain("understood-label");  // no visible label: it reads as the job itself
  expect(html).toContain("weekdays 9 AM to 6 PM IST");
  expect(html).toContain("clamped");   // three lines, the rest on More
  expect(html).toContain(">More<");
  expect(html).toContain(">Change<");
});

it("is nothing when there is no brief, and a short one needs no More", () => {
  expect(renderToStaticMarkup(<Understood text="  " />)).toBe("");
  const html = renderToStaticMarkup(<Understood text="Remind me to drink water every hour." />);
  expect(html).not.toContain(">More<");
  expect(html).not.toContain("clamped");
});
