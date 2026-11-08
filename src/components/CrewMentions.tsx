import type { CrewInstance } from "../types";
import { agentStyle } from "./CrewFan";
import { nextRun } from "./CrewQuickList";

/** The @ being typed right before the caret: where it starts and what follows it ("@News W").
 *  A name may hold spaces, so the query runs to the caret; null when no @ is open. */
export function mentionQuery(text: string, caret: number): { start: number; query: string } | null {
  const found = /(^|\s)@([^@\n]{0,40})$/.exec(text.slice(0, caret));
  return found ? { start: found.index + found[1].length, query: found[2] } : null;
}

/** The person's Crew that fit what follows the @: names starting with it first, then any
 *  containing it. Drafts are not Crew yet. */
export function mentionMatches(crews: CrewInstance[], query: string): CrewInstance[] {
  const q = query.trim().toLowerCase();
  const real = crews.filter(c => c.status !== "draft");
  if (!q) return real.slice(0, 8);
  const starts = real.filter(c => c.name.toLowerCase().startsWith(q));
  const contains = real.filter(c => !starts.includes(c) && c.name.toLowerCase().includes(q));
  return [...starts, ...contains].slice(0, 8);
}

/** The text with the open @query replaced by the chosen Crew's name, and where the caret goes. */
export function insertMention(text: string, start: number, caret: number, name: string): { text: string; caret: number } {
  const mention = `@${name} `;
  return { text: text.slice(0, start) + mention + text.slice(caret), caret: start + mention.length };
}

function initials(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}

/** The list that opens above the message box when the person types @. */
export function CrewMentionMenu({ crews, active, onPick, onHover }: {
  crews: CrewInstance[]; active: number; onPick: (crew: CrewInstance) => void; onHover: (index: number) => void;
}) {
  if (!crews.length) return null;
  return <div className="mention-menu" role="listbox" aria-label="Your Crew">
    <p className="mention-head">Your Crew</p>
    {crews.map((crew, i) => {
      const next = nextRun(crew);
      return <button key={crew.id} type="button" role="option" aria-selected={i === active} className="mention-item" data-active={i === active}
        onMouseDown={e => { e.preventDefault(); onPick(crew); }} onMouseEnter={() => onHover(i)}>
        <span className="mention-face" style={agentStyle(crew)}>{initials(crew.name)}</span>
        <span className="min-w-0 flex-1 truncate">{crew.name}</span>
        <span className="mention-next" data-tone={next.tone}>{next.text}</span>
      </button>;
    })}
    <p className="mention-hint">↑↓ to choose · Enter to add · Esc to close</p>
  </div>;
}

/** The text cut into plain parts and @Crew parts: a tag counts only for a real Crew's whole name,
 *  at the start or after a space, so "me@x.com" and a half-typed "@New" stay plain. */
export function mentionParts(text: string, names: string[]): { text: string; crew: boolean }[] {
  const sorted = [...new Set(names.filter(Boolean))].sort((a, b) => b.length - a.length);
  const parts: { text: string; crew: boolean }[] = [];
  let plain = "", i = 0;
  const lower = text.toLowerCase();
  while (i < text.length) {
    const at = text[i] === "@" && (i === 0 || /\s/.test(text[i - 1]));
    const name = at ? sorted.find(n => lower.startsWith(n.toLowerCase(), i + 1) && !/[\p{L}\p{N}]/u.test(text[i + 1 + n.length] ?? "")) : undefined;
    if (name) {
      if (plain) parts.push({ text: plain, crew: false });
      parts.push({ text: text.slice(i, i + 1 + name.length), crew: true });
      plain = ""; i += 1 + name.length;
    } else { plain += text[i]; i += 1; }
  }
  if (plain) parts.push({ text: plain, crew: false });
  return parts;
}

/** Text with each @Crew in its own colour (the message box and the chat alike). */
export function MentionText({ text, names }: { text: string; names: string[] }) {
  return <>{mentionParts(text, names).map((p, i) => p.crew ? <span key={i} className="mention-tag">{p.text}</span> : p.text)}</>;
}
