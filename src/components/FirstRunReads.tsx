import type {FirstRunResult} from '../lib/firstRun';

const NAMES:Record<string,[string,string]>={gmail:['Gmail','emails'],calendar:['Calendar','events'],tasks:['Tasks','tasks'],drive:['Drive','files'],contacts:['Contacts','contacts'],youtube:['YouTube','videos']};

/** One line: what the test looked at. Counts come from read receipts, never from the model. */
export function FirstRunReads({result}:{result?:FirstRunResult}) {
  // A test on a sample read nothing of the person's: the sample note says what it used.
  if(result?.status!=='passed'||result.tested_with==='sample')return null;
  const rows=Object.values(result.resource_checks??{}).flatMap(checks=>
    Array.isArray(checks)?checks.filter(check=>check&&typeof check.resource==='string'&&Number.isFinite(check.item_count)):[]);
  if(!rows.length)return null;
  const seen=new Map<string,number>();
  for(const read of rows)seen.set(read.resource,Math.max(seen.get(read.resource)??0,read.item_count));
  const parts=[...seen].map(([resource,count])=>{const [name,unit]=NAMES[resource]??[resource,'items'];return `${name} (${count} ${unit})`;});
  return <p className="preview-note" aria-label="Setup resource reads">Looked at {parts.join(', ')}.</p>;
}
