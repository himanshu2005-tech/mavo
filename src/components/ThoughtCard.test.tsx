import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ThoughtCard, isCard } from "./ThoughtCard";

it("draws each action as a card: what, about what, and how it ended", () => {
  const sent = renderToStaticMarkup(<ThoughtCard thought={{ label: "", text: "", tool: "gmail.send", outcome: "done", about: "To Ravi: Friday 10:00 works." }} />);
  expect(sent).toContain(">Reply<");
  expect(sent).toContain(">Done<");
  expect(sent).toContain("To Ravi: Friday 10:00 works.");
  const refused = renderToStaticMarkup(<ThoughtCard thought={{ label: "", text: "", tool: "calendar.create", outcome: "refused", about: "Mon 16 Nov, 3:00–3:30 PM", result: "refused: book only a time they offered" }} />);
  expect(refused).toContain('data-outcome="refused"');
  expect(refused).toContain("book only a time they offered");
  expect(refused).not.toContain("refused:");
  const checked = renderToStaticMarkup(<ThoughtCard thought={{ label: "", text: "", tool: "find_free_times", outcome: "done", result: "Free: Fri 20 Nov, 10:00–10:45 AM" }} />);
  expect(checked).toContain("Free: Fri 20 Nov");
});

it("leaves plain thinking as text", () => {
  expect(isCard({ label: "", text: "Reading it.", tool: "read" })).toBe(false);
  expect(isCard({ label: "", text: "Thinking" })).toBe(false);
  expect(isCard({ label: "", text: "", tool: "gmail.send" })).toBe(true);
});

it("draws browser steps too: the page it opened, what it read, a failed step with why", () => {
  const opened = renderToStaticMarkup(<ThoughtCard thought={{ label: "", text: "", tool: "browser.open", outcome: "done", about: "airouter.in/dashboard", result: "Dashboard · AIRouter" }} />);
  expect(opened).toContain(">Open<");
  expect(opened).toContain("airouter.in/dashboard");
  expect(opened).toContain("Dashboard · AIRouter");
  expect(opened).not.toContain(">Done<");  // a step that only looks says what it saw
  const failed = renderToStaticMarkup(<ThoughtCard thought={{ label: "", text: "", tool: "browser.navigate", outcome: "failed", about: "credits", result: "No link named credits." }} />);
  expect(failed).toContain(">Go to<");
  expect(failed).toContain("No link named credits.");
  expect(failed).toContain('data-outcome="failed"');
});
