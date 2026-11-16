import { useId, type CSSProperties, type ReactNode } from "react";

/* Each agent is an orb of its own: colours from its identity, mixed by its id, so the same agent
   always looks the same and two agents rarely look alike. It shows what it is doing by how its
   colours move. (botParts below is the older robot's, kept for anything that still reads it.) */

export type BotState = "idle" | "ready" | "working" | "needs" | "stopped" | "done";

function hash(seed: string): number {
  let value = 2166136261;
  for (const ch of seed) value = Math.imul(value ^ ch.charCodeAt(0), 16777619);
  value = Math.imul(value ^ (value >>> 15), 0x2c1b3c6d);
  return (value ^ (value >>> 13)) >>> 0;
}

const PALETTE = ["blue", "violet", "teal", "orange", "amber", "rose"];

/** One of Mavo's six colours for something with no identity colour yet (a library agent). */
export function paletteStyle(seed: string): CSSProperties {
  const h = Math.imul(hash(seed) ^ 0x9e3779b9, 0x85ebca6b) >>> 0;
  return { ["--identity" as string]: `var(--c-${PALETTE[(h >>> 16) % PALETTE.length]})` };
}

/** The parts a bot is made of, picked from its seed. */
export function botParts(seed: string) {
  const h = hash(seed || "?");
  return { head: h % 4, antenna: (h >>> 3) % 4, eyes: (h >>> 6) % 5, mouth: (h >>> 9) % 4, blink: (h >>> 12) % 7 };
}

const HEADS = [
  <rect key="h" x="3.5" y="6.5" width="17" height="14" rx="2.5" />,
  <rect key="h" x="2.5" y="8" width="19" height="12.5" rx="2.5" />,
  <rect key="h" x="4.5" y="5.5" width="15" height="15" rx="2.5" />,
  <path key="h" d="M7 6.5h10l3.5 3.5v7L17 20.5H7L3.5 17v-7z" />,
];
/** Top of each head, where an antenna sits. */
const TOPS = [6.5, 8, 5.5, 6.5];

function antenna(kind: number, top: number, needs: boolean): ReactNode {
  const tip = "bot-tip" + (needs ? " is-needs" : "");
  switch (kind) {
    case 1: return <><path className="bot-line" d={`M12 ${top}V3.2`} /><rect className={tip} x="10.6" y="1.4" width="2.8" height="2.8" rx=".6" /></>;
    case 2: return <><path className="bot-line" d={`M8.5 ${top}V4.2M15.5 ${top}V4.2`} /><rect className={tip} x="7.5" y="2.6" width="2" height="2" rx=".4" /><rect className={tip} x="14.5" y="2.6" width="2" height="2" rx=".4" /></>;
    case 3: return <><rect className="bot-ear" x=".8" y="11" width="2.2" height="5" rx=".6" /><rect className="bot-ear" x="21" y="11" width="2.2" height="5" rx=".6" />
      {needs && <rect className={tip} x="10.8" y="2.4" width="2.4" height="2.4" rx=".5" />}</>;
    default: return needs ? <rect className={tip} x="10.8" y="2.4" width="2.4" height="2.4" rx=".5" /> : null;
  }
}

function eyes(kind: number, state: BotState): ReactNode {
  if (state === "stopped") return <path className="bot-line" d="M7.4 13h3.2M13.4 13h3.2" />;
  if (state === "done") return <path className="bot-line" d="M7.4 13.6l1.6-1.8 1.6 1.8M13.4 13.6l1.6-1.8 1.6 1.8" />;
  switch (kind) {
    case 1: return <><rect x="7.4" y="11.6" width="2.8" height="2.8" rx=".5" /><rect x="13.8" y="11.6" width="2.8" height="2.8" rx=".5" /></>;
    case 2: return <><rect className="bot-visor" x="6.5" y="11.4" width="11" height="3" rx="1" /><rect className="bot-glint" x="8.2" y="12.2" width="2.4" height="1.4" rx=".4" /></>;
    case 3: return <rect x="9.8" y="11.2" width="4.4" height="3.6" rx=".8" />;
    case 4: return <><rect x="7.6" y="11.4" width="2.4" height="3.2" rx=".5" /><rect x="14" y="11.4" width="2.4" height="3.2" rx=".5" /></>;
    default: return <><circle cx="9" cy="13" r="1.5" /><circle cx="15" cy="13" r="1.5" /></>;
  }
}

function mouth(kind: number, bottom: number): ReactNode {
  const y = bottom - 3.4;
  switch (kind) {
    case 1: return <path className="bot-line" d={`M10 ${y}h4`} />;
    case 2: return <>{[9.6, 11.4, 13.2].map(x => <rect key={x} x={x} y={y - .6} width="1.2" height="1.2" rx=".2" />)}</>;
    case 3: return <rect x="11" y={y - .7} width="2" height="1.4" rx=".3" />;
    default: return null;
  }
}

/** The hues of one agent's orb: its own identity hue (when the page gives one, as --hue) blended
 *  with two neighbours picked from its seed, so each agent's orb is its own mix of colours. */
export function orbHues(seed: string) {
  const h = hash(seed || "?");
  // A wide blend, as Mavo's own once was: its hue, and two far neighbours, its own mix.
  return { base: h % 360, near: 40 + ((h >>> 9) % 60), far: 80 + ((h >>> 15) % 90), turn: 10 + ((h >>> 21) % 8), delay: (h >>> 4) % 9 };
}

/** One agent, as a living orb (like Siri): soft colour blobs swirling inside a glass sphere, in
 *  that agent's own colours. Calm while it waits, quick and bright while it works, an amber glow
 *  when it needs you, grey and still when stopped. `seed` is what makes it its own (the agent's
 *  id, or a library agent's name). Sized by its container. Motion lives in bots.css and stops for
 *  reduced motion. */
export function AgentBot({ seed, state = "idle", label, className }: { seed: string; state?: BotState; label?: string; className?: string }) {
  const uid = useId().replace(/:/g, "");
  if (seed === "mavo") return <MavoOrb uid={uid} state={state} label={label} className={className} />;
  const o = orbHues(seed);
  const hue = `var(--hue, ${o.base})`;
  const fill = (shift: number, sat: number, light: number) => ({ fill: `hsl(calc(${hue} + ${shift}) ${sat}% ${light}%)` });
  return <svg className={"agent-bot agent-orb " + (className ?? "")} viewBox="0 0 40 40" data-state={state} role={label ? "img" : undefined}
    aria-label={label} aria-hidden={label ? undefined : true} focusable="false"
    style={{ ["--orb-turn" as string]: `${o.turn}s`, ["--orb-delay" as string]: `-${o.delay}s` } as CSSProperties}>
    <defs>
      <clipPath id={`c${uid}`}><circle cx="20" cy="20" r="18.5" /></clipPath>
      <filter id={`b${uid}`} x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3.6" /></filter>
      <radialGradient id={`s${uid}`} cx="34%" cy="28%" r="62%"><stop offset="0" stopColor="#fff" stopOpacity=".55" /><stop offset=".55" stopColor="#fff" stopOpacity="0" /></radialGradient>
      <radialGradient id={`d${uid}`} cx="50%" cy="50%" r="50%"><stop offset=".62" stopColor="#000" stopOpacity="0" /><stop offset="1" stopColor="#000" stopOpacity=".38" /></radialGradient>
    </defs>
    <g clipPath={`url(#c${uid})`}>
      <circle className="orb-base" cx="20" cy="20" r="20" style={fill(0, 55, 18)} />
      <g className="orb-swirl" filter={`url(#b${uid})`}>
        <ellipse className="orb-a" cx="14" cy="15" rx="13" ry="10" style={fill(0, 90, 60)} />
        <ellipse className="orb-b" cx="27" cy="21" rx="12" ry="11" style={fill(o.near, 92, 62)} />
        <ellipse className="orb-c" cx="18" cy="29" rx="12" ry="8" style={fill(-o.far, 88, 58)} />
        {state === "needs" && <ellipse className="orb-call" cx="20" cy="20" rx="11" ry="11" />}
      </g>
      <circle cx="20" cy="20" r="20" fill={`url(#d${uid})`} />
      <circle className="orb-shine" cx="20" cy="20" r="20" fill={`url(#s${uid})`} />
    </g>
    <circle className="orb-rim" cx="20" cy="20" r="18.5" />
  </svg>;
}

/** Mavo itself: a deep glass sphere with aurora ribbons of light moving inside it, a soft halo and
 *  a fine rim. Its own design, apart from the agents' orbs. Same states as theirs. */
function MavoOrb({ uid, state, label, className }: { uid: string; state: BotState; label?: string; className?: string }) {
  const ribbon = (id: string, a: string, b: string) => <linearGradient id={id + uid} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor={a} /><stop offset="1" stopColor={b} /></linearGradient>;
  return <svg className={"agent-bot agent-orb mavo-orb " + (className ?? "")} viewBox="0 0 40 40" data-state={state} role={label ? "img" : undefined}
    aria-label={label} aria-hidden={label ? undefined : true} focusable="false">
    <defs>
      <radialGradient id={`mb${uid}`} cx="50%" cy="42%" r="58%"><stop offset="0" stopColor="#1a1638" /><stop offset=".7" stopColor="#0a0a18" /><stop offset="1" stopColor="#04040a" /></radialGradient>
      {ribbon("r1", "#8b5cff", "#38d6ff")}
      {ribbon("r2", "#ff5fa8", "#ffb45c")}
      {ribbon("r3", "#3cffd0", "#5b7cff")}
      <linearGradient id={`rim${uid}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#fff" stopOpacity=".55" /><stop offset=".5" stopColor="#fff" stopOpacity=".06" /><stop offset="1" stopColor="#9ab8ff" stopOpacity=".35" /></linearGradient>
      <radialGradient id={`ms${uid}`} cx="36%" cy="24%" r="40%"><stop offset="0" stopColor="#fff" stopOpacity=".7" /><stop offset="1" stopColor="#fff" stopOpacity="0" /></radialGradient>
      <filter id={`mg${uid}`} x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.3" /></filter>
      <radialGradient id={`mk${uid}`} cx="50%" cy="55%" r="45%"><stop offset="0" stopColor="#7d6bff" stopOpacity=".75" /><stop offset=".6" stopColor="#4a3bd6" stopOpacity=".25" /><stop offset="1" stopColor="#4a3bd6" stopOpacity="0" /></radialGradient>
      <filter id={`mh${uid}`} x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.4" /></filter>
      <clipPath id={`mc${uid}`}><circle cx="20" cy="20" r="18" /></clipPath>
    </defs>
    <circle className="mavo-halo" cx="20" cy="20" r="18.5" fill="none" stroke={`url(#r1${uid})`} strokeWidth="2.4" filter={`url(#mh${uid})`} />
    <circle cx="20" cy="20" r="18" fill={`url(#mb${uid})`} />
    <g clipPath={`url(#mc${uid})`}>
      <circle className="mavo-core" cx="20" cy="22" r="13" fill={`url(#mk${uid})`} />
      <g className="mavo-ribbons" filter={`url(#mg${uid})`} style={{ mixBlendMode: "screen" }}>
        <path className="mavo-r1" d="M2 24 C 10 12, 22 30, 38 15" fill="none" stroke={`url(#r1${uid})`} strokeWidth="7" strokeLinecap="round" opacity=".9" />
        <path className="mavo-r2" d="M4 15 C 14 27, 26 6, 37 23" fill="none" stroke={`url(#r2${uid})`} strokeWidth="5.5" strokeLinecap="round" opacity=".8" />
        <path className="mavo-r3" d="M6 29 C 16 19, 24 34, 34 9" fill="none" stroke={`url(#r3${uid})`} strokeWidth="5" strokeLinecap="round" opacity=".75" />
        {state === "needs" && <circle className="orb-call" cx="20" cy="20" r="9" />}
      </g>
      <circle cx="20" cy="20" r="18" fill={`url(#ms${uid})`} />
    </g>
    <circle cx="20" cy="20" r="17.6" fill="none" stroke={`url(#rim${uid})`} strokeWidth=".7" />
  </svg>;
}
