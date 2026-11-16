import type {FirstRunResult} from '../lib/firstRun';

const words=(operation:string)=>operation.split('.').pop()!.replaceAll('_',' ');
type Args=Record<string,unknown>;
const text=(v:unknown)=>typeof v==='string'?v.trim():v==null?'':String(v);

/** "Mon 16 Nov, 9:00 – 9:30 am": a person's way of saying when, never an ISO timestamp. */
export function whenText(start:unknown,end?:unknown):string|null{
  const at=(v:unknown)=>{const value=text(v);if(!value)return null;
    const allDay=/^\d{4}-\d{2}-\d{2}$/.test(value);
    const d=new Date(allDay?value+'T00:00:00':value);return isNaN(d.getTime())?null:{d,allDay};};
  const a=at(start);if(!a)return null;
  const day=a.d.toLocaleDateString([], {weekday:'short',day:'numeric',month:'short'});
  if(a.allDay)return `${day}, all day`;
  const clock=(d:Date)=>d.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
  const b=at(end);
  if(!b)return `${day}, ${clock(a.d)}`;
  const sameDay=b.d.toDateString()===a.d.toDateString();
  return sameDay?`${day}, ${clock(a.d)} – ${clock(b.d)}`:`${day}, ${clock(a.d)} – ${b.d.toLocaleDateString([], {weekday:'short',day:'numeric',month:'short'})}, ${clock(b.d)}`;
}

/** What one change would be, as a person reads it. Ids, threads and versions are Mavo's
 *  business, never shown. */
export function Change({operation,label,args}:{operation:string;label?:string;args:Args}){
  const [service,verb]=operation.split('.');
  if(service==='gmail'&&(verb==='send'||verb==='draft')){
    return <div className="change">
      <p className="change-what">{verb==='send'?'Send this email':'Save this email as a draft'}</p>
      <dl className="change-fields"><dt>To</dt><dd>{text(args.to)}</dd><dt>Subject</dt><dd>{text(args.subject)}</dd></dl>
      {text(args.body)&&<blockquote className="change-body">{text(args.body)}</blockquote>}
    </div>;
  }
  if(service==='calendar'){
    const time=whenText(args.start,args.end);
    return <div className="change">
      <p className="change-what">{verb==='delete'?'Remove from your calendar':verb==='update'?'Change on your calendar':'Add to your calendar'}</p>
      <p className="change-title">{text(args.summary)||label||'An event'}</p>
      {time&&<p className="change-when">{time}</p>}
      {text(args.description)&&<p className="change-note">{text(args.description)}</p>}
    </div>;
  }
  if(service==='tasks'){
    const due=whenText(text(args.due).slice(0,10)); // a day: Google writes it as midnight UTC
    return <div className="change">
      <p className="change-what">{verb==='delete'?'Delete a task':verb==='complete'?'Mark a task done':verb==='update'?'Change a task':'Add a task'}</p>
      <p className="change-title">{text(args.title)||label||'A task'}</p>
      {due&&<p className="change-when">Due {due.replace(', all day','')}</p>}
      {text(args.notes)&&<p className="change-note">{text(args.notes)}</p>}
    </div>;
  }
  // Anything else: its readable details, one per line, without ids or raw objects.
  const shown=Object.entries(args).filter(([k,v])=>!/(^|_)(id|ids|etag|thread_id|in_reply_to)$/.test(k)&&(typeof v==='string'||typeof v==='number'||typeof v==='boolean')&&text(v));
  return <div className="change">
    <p className="change-what">{label??words(operation)}</p>
    {!!shown.length&&<dl className="change-fields">{shown.map(([k,v])=><div key={k} className="contents"><dt>{k.replaceAll('_',' ')}</dt><dd>{text(v)}</dd></div>)}</dl>}
  </div>;
}

/** What the test run would have done, as a person reads it. Nothing was changed, so it says so. */
export function CrewChangePreview({result}:{result?:FirstRunResult}){
  if(result?.status!=='passed'||result.kind!=='action_preview'||result.confirmed)return null;
  if(result.tested_with==='sample')return null; // a test on an example shows only how it went
  const actions=result.proposed_actions??[];
  const others=(result.unexercised_actions??[]).map(words);
  const asks=actions.some(a=>a.not_verified?.includes('script_approval'));
  return <section className="preview" aria-label="Proposed changes">
    <p className="preview-title">{actions.length?(result.tested_with==='sample'?'With the sample, it would:':'In the test, it would:'):'Test passed. Nothing needed doing this time.'}</p>
    {!!actions.length&&<ul className="changes" aria-label="Validated action proposals">{actions.map((action,index)=>
      <li key={index}><Change operation={action.operation} label={action.label} args={(action.arguments??{}) as Args}/></li>)}</ul>}
    {!!others.length&&<p className="preview-note">Also allowed to {others.join(', ')}.</p>}
    {asks&&<p className="preview-note">It asks you before running a script.</p>}
    <p className="preview-note">{result.tested_with==='sample'?'Only a test on a sample: nothing was sent or changed.':'Nothing was changed yet.'}</p>
  </section>;
}
