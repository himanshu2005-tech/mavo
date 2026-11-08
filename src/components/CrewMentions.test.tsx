import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { CrewMentionMenu, MentionText, insertMention, mentionMatches, mentionParts, mentionQuery } from "./CrewMentions";
import type { CrewInstance } from "../types";

const crew = (id: string, name: string, status = "active") => ({ id, name, status, template_id: "research", is_running: true, runtime_state: "waiting", trigger_type: "interval", next_run_at: null, configuration_plan: {} }) as unknown as CrewInstance;
const crews = [crew("a", "Inbox Summary"), crew("b", "Placements Job Watch"), crew("c", "News Watch"), crew("d", "Draft Thing", "draft")];

it("opens on @ and follows what is typed after it, spaces and all", () => {
  expect(mentionQuery("ask @", 5)).toEqual({ start: 4, query: "" });
  expect(mentionQuery("@News W", 7)).toEqual({ start: 0, query: "News W" });
  expect(mentionQuery("email me@x", 10)).toBeNull(); // an address is not a mention
  expect(mentionQuery("no at here", 10)).toBeNull();
});

it("lists only real Crew, names that start with the words first", () => {
  expect(mentionMatches(crews, "").map(c => c.id)).toEqual(["a", "b", "c"]);
  expect(mentionMatches(crews, "wat").map(c => c.name)).toEqual(["Placements Job Watch", "News Watch"]);
  expect(mentionMatches(crews, "news").map(c => c.name)).toEqual(["News Watch"]);
  expect(mentionMatches(crews, "draft")).toEqual([]);
});

it("puts @Name in the message where the @ was, and the caret after it", () => {
  expect(insertMention("ask @inb now", 4, 8, "Inbox Summary")).toEqual({ text: "ask @Inbox Summary  now", caret: 19 });
  const markup = renderToStaticMarkup(<CrewMentionMenu crews={crews.slice(0, 2)} active={1} onPick={() => undefined} onHover={() => undefined} />);
  expect(markup).toContain("Inbox Summary");
  expect(markup).toContain('aria-selected="true"');
});

it("colours a tag only for a real Crew's whole name", () => {
  const names = ["Inbox Summary", "Inbox Summary 2", "Lunch Reminder"];
  const tags = (text: string) => mentionParts(text, names).filter(p => p.crew).map(p => p.text);
  expect(tags("whenever my @Lunch Reminder works, sleep")).toEqual(["@Lunch Reminder"]);
  expect(tags("@inbox summary 2 and @Inbox Summary")).toEqual(["@inbox summary 2", "@Inbox Summary"]); // longest first
  expect(tags("mail me@lunch reminder.com")).toEqual([]); // not after a space
  expect(tags("@Lunch Reminders are fun")).toEqual([]); // not a whole name
  expect(tags("@News")).toEqual([]); // half-typed, not a Crew
  expect(mentionParts("a @Lunch Reminder b", names).map(p => p.text).join("")).toBe("a @Lunch Reminder b"); // nothing lost
  expect(renderToStaticMarkup(<MentionText text="tell @Lunch Reminder" names={names} />)).toBe('tell <span class="mention-tag">@Lunch Reminder</span>');
});
