import { useEffect, useMemo, useRef, useState } from 'react';
import { Bell, Brain, Check, CalendarDays, CalendarPlus, CalendarSearch, Database, FileText, Inbox, ListChecks, Mail, Send, Split, type LucideIcon } from 'lucide-react';
import '../crew-members.css';

/** A workflow step as Mavo stores it (backend task_plan.TaskStep). */
export interface FlowStep {
  id: string; kind: string; title?: string; run_text?: string | null; instructions?: string; source?: string | null; new_only?: boolean;
  role?: string | null; uses?: string[]; operation?: string | null; args?: Record<string, unknown>; args_from?: string | null;
  automation?: string | null; params?: Record<string, unknown>; when?: Gate | null; where?: Gate | null;
  returns?: { list?: boolean; fields?: Record<string, unknown> } | null;
}
interface Gate { step: string; field?: string; in?: string[]; equals?: unknown; min?: number; max?: number; is?: 'empty' | 'not_empty'; label?: string }

const READS: Record<string, [string, LucideIcon]> = {
  gmail: ['Your mail', Inbox], calendar: ['Your calendar', CalendarDays], tasks: ['Your tasks', ListChecks],
  drive: ['Your files', FileText], contacts: ['Your contacts', Database], memory: ['What you told Mavo', Database], files: ['Your saved files', FileText],
};
const ACTS: Record<string, [string, LucideIcon]> = {
  'calendar.create': ['Book it on your calendar', CalendarPlus], 'calendar.update': ['Move the event', CalendarPlus],
  'calendar.delete': ['Remove the event', CalendarPlus], 'gmail.send': ['Send the email', Send], 'gmail.draft': ['Draft the email', Mail],
  'tasks.create': ['Add a task', ListChecks], 'notes.write': ['Write a note', FileText], 'notifications.show': ['Alert you', Bell],
  ask: ['Ask you', Bell],
};
const KIND: Record<string, string> = { read: 'Reads', helper: 'Agent', agent: 'Agent', automation: 'Code', act: 'Action' };

const words = (field: string) => field.replaceAll('_', ' ').replaceAll('.', ' ');

/** "If the sender cancels": a branch as the person reads it (Core's own words, when given). */
export function gateText(gate?: Gate | null): string | null {
  if (!gate?.field) return null;
  if (gate.label?.trim()) return `If ${gate.label.trim().replace(/^if\s+/i, '')}`;
  const f = words(gate.field);
  if (gate.in?.length) return `when ${f} is ${gate.in.join(' or ')}`;
  if (gate.equals !== undefined) return `when ${f} is ${String(gate.equals)}`;
  if (gate.min !== undefined && gate.max !== undefined) return `when ${f} is ${gate.min} to ${gate.max}`;
  if (gate.min !== undefined) return `when ${f} is at least ${gate.min}`;
  if (gate.max !== undefined) return `when ${f} is at most ${gate.max}`;
  if (gate.is) return gate.is === 'empty' ? `when there is no ${f}` : `when ${f} is set`;
  return null;
}

/** A step's name and icon as the person reads it ("Your mail", "Decides what to do"). */
export function look(step: FlowStep): [string, LucideIcon] {
  if (step.kind === 'read') {
    const [name, icon] = READS[step.source ?? ''] ?? [`Reads ${step.source}`, FileText];
    return [name + (step.new_only ? ', new only' : ''), icon];
  }
  if (step.kind === 'act') return ACTS[step.operation ?? ''] ?? [step.title || step.operation || 'Act', Send];
  if (step.kind === 'automation' && step.automation === 'calendar.slots') return ['Find a free time', CalendarSearch];
  if (step.kind === 'automation' && step.automation === 'agent.desk') return ['Decides what to do', Brain];
  if (step.kind === 'automation' && step.automation === 'calendar.check') return ['Checks your calendar on those days', CalendarSearch];
  return [step.title || step.role || step.id, Brain];
}

// What each action is for, as a branch out of the agent's decision.
const WHEN: Record<string, string> = {
  'calendar.create': 'their time is free, or they agreed', 'calendar.update': 'they move a meeting it booked',
  'calendar.delete': 'they cancel a meeting it booked', 'gmail.send': 'to reply', 'gmail.draft': 'to reply',
};

/** The actions a crew's agents may take, as its plan lists them. */
export function crewActions(plan: unknown): string[] {
  const agents = (plan as { composition?: { agents?: { actions?: string[] }[] } } | undefined)?.composition?.agents ?? [];
  return [...new Set(agents.flatMap(a => a.actions ?? []))];
}

/** Where the guarded agent is on its diagram, from what it records as it works: the step it is
 *  on, the ones it went through for this item, and one whose action was refused. */
export function liveNodes(steps: FlowStep[], thoughts: { tool?: string; outcome?: string }[]): { active: string | null; done: string[]; refused: string | null } {
  const reads = steps.filter(s => s.kind === 'read');
  const calendar = reads.find(s => s.source === 'calendar')?.id;
  const node = (tool?: string) => !tool ? null : tool === 'read' || tool === 'answer' ? 'desk_read'
    : tool === 'find_free_times' ? calendar ?? 'desk' : tool === 'ask_you' ? 'ask_you' : ACTS[tool] ? `do_${tool}` : 'desk';
  const start = thoughts.map(t => t.tool).lastIndexOf('read');
  const now = thoughts.slice(Math.max(0, start)).filter(t => node(t.tool));
  if (!now.length) return { active: null, done: [], refused: null };
  const last = now[now.length - 1];
  const done = new Set(reads.filter(s => s.id !== calendar).map(s => s.id));
  for (const t of now.slice(0, -1)) done.add(node(t.tool)!);
  if (now.length > 1) done.add('desk');  // it decided at least once
  const active = node(last.tool);
  return { active, done: [...done], refused: last.outcome === 'refused' ? active : null };
}

/** The guarded agent's run in the order it happens: the new item, the agent reading it, code
 *  checking the calendar on exactly the days it names, the agent deciding, then each action it
 *  may take, or asking you. Other paths are shown as they are. */
export function deskFlow(steps: FlowStep[], actions: string[] = []): FlowStep[] {
  const desk = steps.find(s => s.automation === 'agent.desk');
  if (!desk) return steps;
  const reads = steps.filter(s => s.kind === 'read');
  const calendar = reads.find(s => s.source === 'calendar');
  const items = reads.filter(s => s !== calendar).map(s => ({ ...s, uses: [] }));
  const out: FlowStep[] = [...items, {
    id: 'desk_read', kind: 'agent', title: 'Reads it', uses: items.map(s => s.id),
    instructions: 'Works out who wrote, what they want, and which days and times they mean.',
  }];
  let last = 'desk_read';
  if (calendar) {
    out.push({ id: calendar.id, kind: 'automation', automation: 'calendar.check', uses: [last], params: desk.params });
    last = calendar.id;
  }
  out.push({ ...desk, uses: [last] });
  const ops = [...new Set(actions)].filter(op => ACTS[op]);
  for (const op of ops) out.push({ id: `do_${op}`, kind: 'act', operation: op, uses: ['desk'], when: { step: 'desk', field: 'action', label: WHEN[op] ?? `to ${ACTS[op][0].toLowerCase()}` } });
  out.push({ id: 'ask_you', kind: 'act', operation: 'ask', uses: ['desk'], when: { step: 'desk', field: 'action', label: 'it is unsure' } });
  return out;
}

/** What opens on a click: what the agent is told, what it must hand on, a code step's rules. */
function detail(step: FlowStep): string[] {
  const out: string[] = [];
  if (step.kind === 'helper' || step.kind === 'agent') {
    if (step.instructions) out.push(step.instructions);
    const fields = Object.keys(step.returns?.fields ?? {});
    if (fields.length) out.push(`Hands on, checked by code: ${fields.map(words).join(', ')}`);
  }
  if (step.automation === 'agent.desk') {
    const p = (step.params ?? {}) as Record<string, unknown>;
    return [
      `Picks one action at a time. Up to ${p.alternatives ?? 3} times offered; after ${p.max_rounds ?? 3} emails back and forth, it asks you.`,
      'Every action is checked by code before it happens: only free times inside your hours, only people who wrote, replies in the same thread, nothing done twice.',
    ];
  }
  if (step.automation === 'calendar.check') {
    const p = (step.params ?? {}) as Record<string, unknown>;
    const days = Array.isArray(p.days) ? (p.days as string[]).map(d => d.slice(0, 1).toUpperCase() + d.slice(1, 3)).join(', ') : 'Mon to Fri';
    return [
      `Reads your calendar on the days they asked about, however far ahead, and checks those times against ${days}, ${p.start ?? '09:00'} to ${p.end ?? '18:00'}.`,
      'Says whether their time is free; if not, finds free times near it.',
    ];
  }
  if (step.kind === 'automation' && step.params) {
    const p = step.params as Record<string, unknown>;
    const days = Array.isArray(p.days) ? (p.days as string[]).map(d => d.slice(0, 1).toUpperCase() + d.slice(1, 3)).join(', ') : 'Mon to Fri';
    out.push(`${days}, ${p.start ?? '09:00'} to ${p.end ?? '18:00'}, ${p.duration_minutes ?? 30} min` + (p.buffer_minutes ? `, ${p.buffer_minutes} min apart` : '') + (p.alternatives ? `, up to ${p.alternatives} other times` : ''));
    out.push('Takes the first offered time that is free; otherwise finds free times. Your events are never moved.');
  }
  if (step.kind === 'act' && step.args && Object.keys(step.args).length) {
    out.push('Exactly as found: ' + Object.entries(step.args).map(([k, v]) => `${words(k)} = ${String(v).replace(/\{\{\s*item\.([\w.]+)\s*\}\}/g, (_, path: string) => words(path))}`).join('; '));
  }
  return out;
}

const depsOf = (s: FlowStep, ids: Set<string>) =>
  [...new Set([...(s.uses ?? []), s.args_from, s.when?.step, s.where?.step].filter((d): d is string => !!d && ids.has(d) && d !== s.id))];

/** Each step's row: one after everything it reads. */
export function flowRows(steps: FlowStep[]): FlowStep[][] {
  const byId = new Map(steps.map(s => [s.id, s]));
  const ids = new Set(byId.keys());
  const depth = new Map<string, number>();
  const level = (s: FlowStep, seen = new Set<string>()): number => {
    if (depth.has(s.id)) return depth.get(s.id)!;
    if (seen.has(s.id)) return 0;
    seen.add(s.id);
    const found = depsOf(s, ids).reduce((most, d) => Math.max(most, level(byId.get(d)!, seen) + 1), 0);
    depth.set(s.id, found);
    return found;
  };
  steps.forEach(s => level(s));
  const rows: FlowStep[][] = [];
  steps.forEach(s => { const n = depth.get(s.id) ?? 0; (rows[n] ??= []).push(s); });
  return rows.filter(Boolean);
}

/** An agent's whole path, as it runs: what it reads, where an agent thinks, what code checks,
 *  each case's branch, and what it does. Lines join each step to what it reads; details open
 *  on a click. */
export function WorkflowFlow({ steps: given, final, active, actions, done = [], refused = null }: { steps: FlowStep[]; final?: string | null; active?: string | null; actions?: string[]; done?: string[]; refused?: string | null }) {
  const actionKey = (actions ?? []).join('|');
  // Kept the same between renders: the lines are redrawn whenever the steps change.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const steps = useMemo(() => deskFlow(given, actions), [given, actionKey]);
  const [open, setOpen] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [lines, setLines] = useState<{ d: string; key: string; to: string }[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ids = new Set(steps.map(s => s.id));
    const draw = () => {
      const base = el.getBoundingClientRect();
      const at = (id: string) => el.querySelector<HTMLElement>(`[data-node="${CSS.escape(id)}"]`)?.getBoundingClientRect();
      const out: { d: string; key: string; to: string }[] = [];
      for (const s of steps) for (const from of depsOf(s, ids)) {
        const a = at(from), b = at(s.id);
        if (!a || !b || b.top <= a.bottom) continue;
        const x1 = a.left + a.width / 2 - base.left, y1 = a.bottom - base.top;
        const x2 = b.left + b.width / 2 - base.left, y2 = b.top - base.top;
        const mid = (y1 + y2) / 2;
        out.push({ key: `${from}>${s.id}`, to: s.id, d: `M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}` });
      }
      setLines(out);
      setSize({ w: base.width, h: base.height });
    };
    draw();
    const watch = new ResizeObserver(draw);
    watch.observe(el);
    return () => watch.disconnect();
  }, [steps, open]);
  if (!steps?.length) return null;
  const rows = flowRows(steps);
  return <div className="wf" ref={box} aria-label="How it works">
    <svg className="wf-lines" width={size.w} height={size.h} aria-hidden="true">
      {lines.map(l => <path key={l.key} d={l.d} data-live={l.to === active || undefined} />)}
      {/* The work moving into the step it is on now. */}
      {lines.filter(l => l.to === active).map(l => <circle key={`dot-${l.key}`} className="wf-dot" r="3.5">
        <animateMotion dur="1.4s" repeatCount="indefinite" path={l.d} />
      </circle>)}
    </svg>
    {rows.map((row, i) => <div key={i} className="wf-row" style={{ ['--n' as string]: Math.min(row.length, 3) }}>
      {row.map(step => {
        const gate = gateText(step.where) ?? gateText(step.when);
        const more = detail(step);
        const shown = open === step.id;
        const [name, Icon] = look(step);
        return <div key={step.id} className="wf-cell" data-node={step.id}>
          {gate && <span className="wf-gate"><Split size={11} aria-hidden="true" />{gate}</span>}
          <div className="wf-node" data-kind={step.automation === 'agent.desk' ? 'agent' : step.kind} data-final={step.id === final || undefined} data-active={step.id === active || undefined}
            data-done={(done.includes(step.id) && step.id !== active) || undefined} data-refused={step.id === refused || undefined}>
            {done.includes(step.id) && step.id !== active && <span className="wf-tick" aria-label="Done"><Check size={10} /></span>}
            {(() => {
              const inner = <><span className="wf-ico" aria-hidden="true"><Icon size={14} /></span>
                <span className="wf-text"><span className="wf-kind">{step.automation === 'agent.desk' ? 'Agent' : KIND[step.kind] ?? step.kind}</span><span className="wf-title">{name}</span></span></>;
              // Only a step with details is a button; the others are plain, never shown as disabled.
              return more.length
                ? <button type="button" className="wf-head" aria-expanded={shown} onClick={() => setOpen(shown ? null : step.id)}>{inner}</button>
                : <div className="wf-head">{inner}</div>;
            })()}
            {shown && <div className="wf-detail">{more.map((line, n) => <p key={n}>{line}</p>)}</div>}
          </div>
        </div>;
      })}
    </div>)}
  </div>;
}
