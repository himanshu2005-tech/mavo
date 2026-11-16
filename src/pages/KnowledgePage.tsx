import { Check, FileText, Pencil, Plus, Trash2, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { api } from "../lib/api";
import { saveFiles, openFile } from "../lib/savedFiles";
import type { MemoryFact, SavedFile } from "../types";

/** What Mavo knows about you and the files you keep with it. Every agent uses both: your
 *  LinkedIn on a form, your résumé on a job application. */
export function KnowledgePage() {
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [files, setFiles] = useState<SavedFile[]>([]);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let live = true;
    void Promise.all([api.memory(), api.files()]).then(([m, f]) => { if (live) { setFacts(m); setFiles(f); } }).catch(e => { if (live) setError(e instanceof Error ? e.message : "Could not load."); });
    return () => { live = false; };
  }, []);
  async function run(work: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : "That did not work."); } finally { setBusy(false); }
  }
  const add = () => run(async () => { if (!text.trim()) return; setFacts(await api.addMemory(text.trim())); setText(""); });
  const save = () => run(async () => { if (!editing) return; setFacts(await api.editMemory(editing.id, editing.text.trim())); setEditing(null); });
  const forget = (id: string) => run(async () => setFacts(await api.forgetMemory(id)));
  const upload = (list: FileList | null) => run(async () => { if (list?.length) { await saveFiles(list); setFiles(await api.files()); } });
  const remove = (file: SavedFile) => run(async () => { if (!window.confirm(`Remove ${file.name}?`)) return; await api.deleteFile(file.id); setFiles(await api.files()); });
  return <>
    <PageHeader eyebrow="Knowledge" title="Knowledge" description="What Mavo knows about you, and your files. Every agent uses them." />
    <div className="page-content space-y-8">
      {error && <p role="alert" className="notice error-notice">{error}</p>}
      <section className="knowledge" aria-label="About you">
        <div className="knowledge-head"><h2>About you</h2><span>{facts.length}</span></div>
        <form className="knowledge-add" onSubmit={e => { e.preventDefault(); void add(); }}>
          <input className="form-control !mt-0" placeholder="My LinkedIn is linkedin.com/in/…" value={text} maxLength={400} onChange={e => setText(e.target.value)} aria-label="Add something Mavo should know" />
          <button className="btn-secondary" disabled={busy || !text.trim()}><Plus size={14} />Add</button>
        </form>
        <p className="knowledge-note">Or just say it in the chat. Never add passwords: save logins in Mavo's browser instead.</p>
        <ul>{[...facts].reverse().map(fact => <li key={fact.id}>
          {editing?.id === fact.id
            ? <form className="knowledge-edit" onSubmit={e => { e.preventDefault(); void save(); }}>
                <input className="form-control !mt-0" autoFocus value={editing.text} maxLength={400} onChange={e => setEditing({ ...editing, text: e.target.value })} aria-label="Edit" />
                <button className="btn-quiet !px-2" aria-label="Save" disabled={busy || editing.text.trim().length < 2}><Check size={14} /></button>
                <button type="button" className="btn-quiet !px-2" aria-label="Cancel" onClick={() => setEditing(null)}><X size={14} /></button>
              </form>
            : <><p>{fact.text}</p><small>{fact.source}</small>
                <button className="btn-quiet !px-2" aria-label={`Edit ${fact.text}`} onClick={() => setEditing({ id: fact.id, text: fact.text })}><Pencil size={13} /></button>
                <button className="btn-quiet !px-2" aria-label={`Forget ${fact.text}`} disabled={busy} onClick={() => void forget(fact.id)}><Trash2 size={13} /></button></>}
        </li>)}</ul>
        {!facts.length && <p className="knowledge-note">Nothing yet.</p>}
      </section>
      <section className="knowledge" aria-label="Files">
        <div className="knowledge-head"><h2>Files</h2><span>{files.length}</span>
          <button className="btn-secondary ml-auto" disabled={busy} onClick={() => picker.current?.click()}><Upload size={14} />Add files</button>
          <input ref={picker} type="file" multiple hidden accept=".pdf,.docx,.txt,.md,.csv,.json,.png,.jpg,.jpeg" onChange={e => { void upload(e.target.files); e.target.value = ""; }} /></div>
        <p className="knowledge-note">Your résumé, cover letter, certificates. Agents can attach these, and only these, to forms such as job applications.</p>
        <ul>{files.map(file => <li key={file.id}>
          <FileText size={14} className="flex-none text-zinc-500" />
          <button className="knowledge-file" onClick={() => void openFile(file).catch(e => setError(e instanceof Error ? e.message : "Could not open it."))}>{file.name}</button>
          <small>{size(file.size)}</small>
          <button className="btn-quiet !px-2" aria-label={`Remove ${file.name}`} disabled={busy} onClick={() => void remove(file)}><Trash2 size={13} /></button>
        </li>)}</ul>
        {!files.length && <p className="knowledge-note">No files yet. Add them here, or attach them in the chat.</p>}
      </section>
    </div>
  </>;
}

function size(bytes: number) {
  return bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
