import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { AgentBot, paletteStyle } from "../components/AgentBot";
import { PageHeader } from "../components/PageHeader";
import { CATEGORIES, LIBRARY, findAgents } from "../lib/library";
import { useAppStore } from "../store/app";

/** Ready-made agents. "Use" puts the request in the chat; setup and a test still come first. */
export function LibraryPage() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const shown = useMemo(() => findAgents(query, category), [query, category]);
  const kinds = useMemo(() => Object.keys(CATEGORIES).filter(c => LIBRARY.some(a => a.category === c)), []);
  return <>
    <PageHeader eyebrow="Library" title="Library" description="Ready-made agents. Pick one, check it in chat, turn it on." />
    <div className="page-content">
      <div className="library-tools">
        <label className="library-search"><Search size={13} aria-hidden="true" /><input aria-label="Search the library" placeholder="Search" value={query} onChange={e => setQuery(e.target.value)} /></label>
        <div className="library-kinds" role="tablist" aria-label="Kinds">
          <button role="tab" aria-selected={category === null} onClick={() => setCategory(null)}>All</button>
          {kinds.map(c => <button key={c} role="tab" aria-selected={category === c} onClick={() => setCategory(c)}>{CATEGORIES[c]}</button>)}
        </div>
      </div>
      {!shown.length && <p className="library-empty">Nothing matches. Describe what you need in the chat instead.</p>}
      <ul className="library-list" aria-label="Ready-made agents">{shown.map(agent => <li key={agent.name}>
        <span className="library-bot" style={paletteStyle(agent.category)} aria-hidden="true"><AgentBot seed={agent.name} state="ready" /></span>
        <div className="min-w-0 flex-1">
          <p>{agent.name}</p>
          <span>{agent.goal}</span>
          <small>{CATEGORIES[agent.category] ?? agent.category} · {agent.when}{agent.uses.length ? ` · ${agent.uses.join(", ")}` : ""}</small>
        </div>
        <button className="btn-secondary" aria-label={`Use ${agent.name}`} onClick={() => useAppStore.getState().startInChat(agent.prompt)}>Use</button>
      </li>)}</ul>
    </div>
  </>;
}
