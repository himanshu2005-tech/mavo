import type { ReactNode } from "react";
import { CrewArt, HUES } from "./CrewArt";

/** Which little scene each page wears. The scenes are the Crew pictures, so the whole app speaks
 * one visual language. */
const SCENES: Record<string, string> = { Crew: "operator", Resources: "computer", Actions: "schedule", Settings: "personal-admin", Chat: "communication" };

/** The top of a page: a banner in the page's colour with its own moving picture. */
export function PageHeader({ eyebrow, title, description, action, scene }: { eyebrow: string; title: string; description: string; action?: ReactNode; scene?: string }) {
  const art = scene ?? SCENES[title] ?? SCENES[eyebrow] ?? "operator";
  return (
    <header aria-label={eyebrow} className="hero" style={{ ["--hue" as string]: HUES[art] ?? 250 }}>
      <div className="hero-copy">
        <h1>{title}</h1>
        <p>{description}</p>
        {action && <div className="page-header-actions hero-actions">{action}</div>}
      </div>
      <div className="hero-scene" aria-hidden="true"><CrewArt template={art} /></div>
    </header>
  );
}
