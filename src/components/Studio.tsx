import type { LucideIcon } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

/* Small shared pieces of the app's look: a coloured icon tile, a status chip, and a card that
   lifts when you point at it. Colour comes from a hue (0-360), the same way Crew pictures get theirs. */

const hue = (value: number): CSSProperties => ({ ["--hue" as string]: value });

export function IconTile({ icon: Icon, hue: h = 250, size = 40, spin = false }: { icon: LucideIcon; hue?: number; size?: number; spin?: boolean }) {
  return <span className="icon-tile" style={{ ...hue(h), width: size, height: size }} aria-hidden="true"><Icon size={Math.round(size * 0.46)} strokeWidth={1.7} className={spin ? "animate-spin" : undefined} /></span>;
}

/** A steady hue for any name, so the same Crew always wears the same colour. */
export function hueFor(name: string): number {
  let h = 7;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

export type Tone = "on" | "off" | "limit" | "warn" | "info";

export function Chip({ tone = "off", children, dot = true }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return <span className="chip" data-tone={tone}>{dot && <i />}{children}</span>;
}

/** A section card with a coloured edge and a header of its own. */
export function Panel({ hue: h = 250, icon, title, note, aside, children, className = "" }: { hue?: number; icon?: LucideIcon; title: string; note?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={"panel " + className} style={hue(h)}>
    <header className="panel-head">
      {icon && <IconTile icon={icon} hue={h} />}
      <div className="min-w-0 flex-1"><h2>{title}</h2>{note && <p>{note}</p>}</div>
      {aside}
    </header>
    {children}
  </section>;
}
