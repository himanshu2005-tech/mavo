import { Bell, CalendarPlus, CalendarSearch, CalendarX, Compass, Eye, Globe, Keyboard, ListChecks, Mail, MousePointerClick, Search, Send } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Thought } from "../types";

const TOOLS: Record<string, [string, LucideIcon]> = {
  "gmail.send": ["Reply", Send], "gmail.draft": ["Draft", Mail],
  "calendar.create": ["Book", CalendarPlus], "calendar.update": ["Move", CalendarPlus], "calendar.delete": ["Remove", CalendarX],
  "find_free_times": ["Check calendar", CalendarSearch], "tasks.create": ["Add task", ListChecks], "ask_you": ["Ask you", Bell],
  "browser.open": ["Open", Globe], "browser.navigate": ["Go to", Compass], "browser.read": ["Read", Eye], "browser.search": ["Search", Search],
  "browser.click": ["Click", MousePointerClick], "browser.fill": ["Type", Keyboard], "browser.select": ["Pick", MousePointerClick], "browser.press": ["Press", Keyboard],
};
/** Steps that only look: their card says what they saw, not "Done". */
const LOOKS = new Set(["find_free_times", "browser.open", "browser.navigate", "browser.read", "browser.search"]);
const WORDS: Record<string, string> = { done: "Done", waiting: "Waiting for your OK", refused: "Refused", asked: "Asked you", failed: "Didn't work" };

/** Whether a step of thinking is an action, drawn as a card. */
export const isCard = (t: Thought) => !!t.tool && t.tool in TOOLS;

/** One action as it happened: what it was, what it was about, and how it ended (with why, when
 *  it was refused). */
export function ThoughtCard({ thought }: { thought: Thought }) {
  const [name, Icon] = TOOLS[thought.tool!];
  const outcome = thought.outcome ?? "done";
  const checked = LOOKS.has(thought.tool!) && (thought.outcome ?? "done") === "done";
  return <div className="act-card" data-outcome={outcome}>
    <span className="act-ico" aria-hidden="true"><Icon size={13} /></span>
    <div className="act-body">
      <p className="act-head"><b>{name}</b>{!checked && <span className="act-state">{WORDS[outcome] ?? outcome}</span>}</p>
      {thought.about && <p className="act-about">{thought.about}</p>}
      {(checked || outcome === "refused" || outcome === "failed" || (outcome === "asked" && LOOKS.has(thought.tool!))) && thought.result && <p className="act-why">{thought.result.replace(/^refused:\s*/i, "")}</p>}
    </div>
  </div>;
}
