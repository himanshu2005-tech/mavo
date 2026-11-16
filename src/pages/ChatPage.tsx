import { ArrowUp, History, LoaderCircle, PanelRightClose, Paperclip, SquarePen, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ChatDialog, ChatHistory } from "../components/ChatHistory";
import { CrewSetupCard } from "../components/CrewSetupCard";
import { TaskRunCard, chatTimeline, useConversationTasks } from "../components/TaskRunCard";
import { CrewMentionMenu, MentionText, insertMention, mentionMatches, mentionQuery } from "../components/CrewMentions";
import { CrewGraph } from "../components/CrewGraph";
import { api } from "../lib/api";
import { sendCoreMessage } from "../lib/chatController";
import { saveFiles } from "../lib/savedFiles";
import {useCoreConnection} from '../lib/useCoreConnection';
import { editMentions } from '../lib/orchestration';
import {identityColor,identityStyle} from '../lib/identityColors';
import { ConversationActivities } from '../components/ConversationActivity';
import { ChatFailure } from '../components/ChatFailure';
import { AgentBot } from '../components/AgentBot';
import { AgentLife } from '../components/AgentLife';
import { typedQuestion } from '../lib/shownText';
import { TypedText } from '../components/TypedText';
import { useAppStore } from "../store/app";
import type { Conversation, CrewInstance, SavedFile } from "../types";

const SUGGESTIONS = [
  "Summarise my important mail every morning",
  "Remind me to drink water every hour",
  "Plan my day tomorrow from my calendar",
];

/** Mavo's chat: messages, and one box to type in. Docked beside the graph, its history opens
 *  from the bar. */
export function ChatPage({ docked = false, onClose, tabs }: { docked?: boolean; onClose?: () => void; tabs?: ReactNode } = {}) {
  const { conversations,activeConversationId,crew,setupDrafts,chatInputs,pendingChatMessages,chatErrors,chatFailures,chatRefinements }=useAppStore();
  const {core,checking,checkConnection}=useCoreConnection();
  const [loading,setLoading]=useState(true);
  const [creating,setCreating]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [dialog,setDialog]=useState<{mode:"rename"|"delete";chat:Conversation}|null>(null);
  const [historyOpen,setHistoryOpen]=useState(false);
  const refineRequest=useAppStore(s=>s.refineRequest);
  const chatFocus=useAppStore(s=>s.chatFocus);
  const [title,setTitle]=useState("");
  const [dialogBusy,setDialogBusy]=useState(false);
  const [dialogError,setDialogError]=useState<string|null>(null);
  const creatingLock=useRef(false);
  // Files attached to the next message: saved in Mavo (Knowledge) as soon as they are picked.
  const [attached,setAttached]=useState<SavedFile[]>([]);
  const [attaching,setAttaching]=useState(false);
  const picker=useRef<HTMLInputElement>(null);
  async function attach(list:FileList|null){
    if(!list?.length)return;
    setAttaching(true);setError(null);
    try{const saved=await saveFiles(list);setAttached(a=>[...a.filter(f=>!saved.some(s=>s.id===f.id)),...saved].slice(0,5));}
    catch(e){setError(e instanceof Error?e.message:"Could not attach that file.");}
    finally{setAttaching(false);}
  }
  const active=useRef(true);
  const bottom=useRef<HTMLDivElement>(null);
  const field=useRef<HTMLTextAreaElement>(null);
  const chat=conversations.find(c=>c.id===activeConversationId);
  const key=chat?.id??"new";
  const input=chatInputs[key]??"";
  const chosenMentions=useAppStore(s=>s.chatMentions[key]??EMPTY_MENTIONS);
  const pendingMessage=pendingChatMessages[key];
  const streaming=useAppStore(s=>s.streamingReplies[key]);
  const [sentFiles,setSentFiles]=useState<string[]>([]);
  const attachedNote=sentFiles.map(name=>`\n📎 ${name}`).join("");
  const failure=chatFailures[key];
  const sending=!!pendingMessage;
  const refining=chatRefinements[key]||undefined;
  const messages=chat?.messages??[];
  // Messages and task cards in the order they happened: a card sits under the reply that started
  // it, not at the bottom of the chat after every later message.
  const runs=useConversationTasks(chat?.id);
  const everyCrew=[...crew,...setupDrafts];
  // Typing @ lists the person's Crew; picking one puts @Name in the message, so Mavo knows
  // exactly which Crew they mean (to ask it, hand it a job, or connect it to another).
  const [caret,setCaret]=useState(0);
  const [mentionAt,setMentionAt]=useState(0);
  const [mentionClosed,setMentionClosed]=useState<string|null>(null);
  const mention=mentionQuery(input,caret);
  const crewNames=crew.filter(c=>c.status!=="draft").flatMap(c=>[c.name,...(c.agents??[]).map(a=>`${c.name}/${a.name}`)]);
  const mirror=useRef<HTMLDivElement>(null);
  const mentions=mention&&mentionClosed!==input?mentionMatches(crew,mention.query):[];
  function pickMention(chosen:import('../components/CrewMentions').MentionTarget){
    if(!mention)return;
    const next=insertMention(input,mention.start,caret,chosen.name);
    useAppStore.setState(s=>({chatInputs:{...s.chatInputs,[key]:next.text},chatMentions:{...s.chatMentions,[key]:[
      ...editMentions(input,next.text,chosenMentions),
      {crewId:chosen.parentCrewId??chosen.id,agentId:chosen.agentId,label:chosen.name,start:mention.start,end:mention.start+chosen.name.length+1},
    ]}}));
    setCaret(next.caret);setMentionAt(0);setMentionClosed(next.text);
    requestAnimationFrame(()=>{field.current?.focus();field.current?.setSelectionRange(next.caret,next.caret);});
  }
  const current=everyCrew.find(c=>c.id===(refining??chat?.planning_crew_id));
  const batch=refining?[]:(chat?.planning_crew_ids??[]).map(id=>everyCrew.find(c=>c.id===id)).filter((c):c is NonNullable<typeof c>=>!!c);
  const shown=batch.length>1?batch:current?[current]:[];
  const links=shown.flatMap(c=>(c.configuration_plan?.upstream??[]).filter(id=>shown.some(s=>s.id===id)).map(id=>({from:id,to:c.id})));
  const timeline=chatTimeline(messages,runs,shown);
  // Replies that arrive while the chat is open type themselves out; those already there do not.
  const seenReplies=useRef<{chat:string;ids:Set<string>}|null>(null);
  if(!seenReplies.current||seenReplies.current.chat!==key)seenReplies.current={chat:key,ids:new Set(messages.map(m=>m.id))};
  // A reply already seen streaming in, word by word, is not typed out a second time.
  const streamed=useRef(false);
  if(streaming)streamed.current=true;
  const freshReply=(id:string)=>{
    const seen=seenReplies.current!.ids;
    if(seen.has(id))return false;
    if(streamed.current){seen.add(id);streamed.current=false;return false;}
    return true;
  };
  useEffect(()=>{
    active.current=true;
    let cancelled=false;
    const epoch=useAppStore.getState().workspaceEpoch;
    const originalHistory=useAppStore.getState().conversations;
    const valid=()=>!cancelled&&active.current&&useAppStore.getState().workspaceEpoch===epoch;
    void (async()=>{
      try{
        const chats=await api.coreConversations();
        if(!valid())return;
        if(useAppStore.getState().conversations===originalHistory)useAppStore.getState().setConversations(chats);
        const refine=sessionStorage.getItem("mavo-refine-crew");
        if(refine){
          sessionStorage.removeItem("mavo-refine-crew");
          const target=chats.find(c=>c.planning_crew_id===refine)??await api.createCoreConversation();
          if(!valid())return;
          useAppStore.getState().upsertConversation(target);
          useAppStore.setState(s=>({chatRefinements:{...s.chatRefinements,[target.id]:refine}}));
        }
      }catch(e){if(valid())setError(String(e));}
      finally{if(valid())setLoading(false);}
    })();
    return()=>{cancelled=true;active.current=false;};
  },[]);
  useEffect(()=>{bottom.current?.scrollIntoView({behavior:"smooth"});},[activeConversationId,messages.length,pendingMessage,current?.id,failure]);
  // The box grows with what is typed, up to 40% of the window, so the whole message shows.
  useLayoutEffect(()=>{const el=field.current;if(!el)return;el.style.height="auto";el.style.height=`${Math.min(el.scrollHeight,Math.round(window.innerHeight*.4))}px`;},[input,docked]);
  useEffect(()=>{
    if(!refineRequest)return;
    useAppStore.getState().refineInChat(null);
    void (async()=>{
      try{
        const target=useAppStore.getState().conversations.find(c=>c.planning_crew_id===refineRequest)??await api.createCoreConversation();
        useAppStore.getState().upsertConversation(target);
        useAppStore.setState(s=>({chatRefinements:{...s.chatRefinements,[target.id]:refineRequest}}));
        field.current?.focus();
      }catch(e){setError(e instanceof Error?e.message:"Could not open that draft in chat.");}
    })();
  },[refineRequest]);
  useEffect(()=>{if(chatFocus)requestAnimationFrame(()=>{const f=field.current;if(!f)return;f.focus();f.setSelectionRange(f.value.length,f.value.length);});},[chatFocus]);
  async function newChat(){
    if(creatingLock.current)return;
    creatingLock.current=true;setCreating(true);setError(null);
    const epoch=useAppStore.getState().workspaceEpoch;
    try{
      const created=await api.createCoreConversation();
      if(useAppStore.getState().workspaceEpoch!==epoch)return;
      useAppStore.getState().upsertConversation(created);
      if(active.current)field.current?.focus();
      return created;
    }catch(e){if(active.current)setError(e instanceof Error?e.message:"Could not create a chat.");}
    finally{creatingLock.current=false;if(active.current)setCreating(false);}
  }
  async function submit(event:FormEvent) {
    event.preventDefault();
    if(!input.trim()||sending||creating||loading||attaching||!core?.connected)return;
    const message=input;
    const files=attached.map(f=>f.id);
    const target=chat??await newChat();
    if(!target)return;
    if(!chat)useAppStore.setState(s=>({chatInputs:{...s.chatInputs,new:""},chatMentions:{...s.chatMentions,[target.id]:chosenMentions}}));
    const ids=[...new Set(chosenMentions.filter(ref=>input.slice(ref.start,ref.end)===`@${ref.label}`).map(ref=>ref.crewId))];
    const agentIds=[...new Set(chosenMentions.filter(ref=>ref.agentId&&input.slice(ref.start,ref.end)===`@${ref.label}`).map(ref=>ref.agentId!))];
    const sent=attached;
    setSentFiles(attached.map(f=>f.name));setAttached([]);
    await sendCoreMessage(target,message,refining,ids,agentIds,files);
    setSentFiles([]);
    // Not sent: the message comes back to the box, and its files with it, for Retry.
    if(useAppStore.getState().chatFailures[target.id])setAttached(now=>now.length?now:sent);
    if(!chat)useAppStore.setState(s=>({chatMentions:{...s.chatMentions,new:[]}}));
  }
  function refine(id:string){
    if(!chat)return;
    useAppStore.setState(s=>({chatRefinements:{...s.chatRefinements,[chat.id]:id}}));
    field.current?.focus();
  }
  function openDialog(mode:"rename"|"delete",item:Conversation){setDialog({mode,chat:item});setTitle(item.title);setDialogError(null);}
  async function confirmDialog(){
    if(!dialog||dialogBusy)return;
    const epoch=useAppStore.getState().workspaceEpoch;
    setDialogBusy(true);setDialogError(null);
    try{
      if(dialog.mode==="rename"){
        const renamed=await api.renameConversation(dialog.chat.id,title.trim());
        if(useAppStore.getState().workspaceEpoch===epoch)useAppStore.getState().upsertConversation(renamed,false);
      }else{
        await api.deleteConversation(dialog.chat.id);
        if(useAppStore.getState().workspaceEpoch===epoch)useAppStore.getState().removeConversation(dialog.chat.id);
      }
      if(active.current)setDialog(null);
    }catch(e){if(active.current)setDialogError(e instanceof Error?e.message:"Could not update this chat.");}
    finally{if(active.current)setDialogBusy(false);}
  }
  // The agents this chat is making: one card each, kept up to date as the chat changes them. On a
  // wide window they sit beside the chat (not stuck between messages); on a narrow one, inline.
  const setupCards=shown.length?<>
    {links.length>0&&<CrewGraph nodes={shown.map(c=>({id:c.id,name:c.name,state:c.runtime_state,running:c.is_running,status:c.status}))} edges={links}/>}
    {shown.map(c=><CrewSetupCard key={c.id+JSON.stringify(c.configuration_plan)} crew={c} onRefine={refine}/>)}
  </>:null;
  const history=<ChatHistory chats={conversations} activeId={activeConversationId} pending={pendingChatMessages} disabled={loading||creating} onNew={()=>{setHistoryOpen(false);void newChat();}} onSelect={id=>{useAppStore.getState().setActiveConversation(id);setError(null);setHistoryOpen(false);}} onRename={item=>openDialog("rename",item)} onDelete={item=>openDialog("delete",item)}/>;
  return <div className={docked?"chat-dock-inner":"flex h-screen min-h-0 flex-col"} data-docked={docked}>
    <div className="chat-workspace">
      {!docked&&history}
      <section className="chat-stage relative flex min-w-0 flex-1 flex-col" aria-label={chat?.title??"New chat"}>
        <AgentLife crews={crew}/>
        {docked&&<header className="chat-dock-bar" aria-label="Assistant">
          {tabs??<h1 className="min-w-0 truncate text-[13px] font-medium text-zinc-300">{chat?.title??"New chat"}</h1>}
          <div className="flex-1"/>
          <div className="relative"><button className="btn-quiet !p-1.5" aria-label="Chats" title="Chats" aria-expanded={historyOpen} onClick={()=>setHistoryOpen(o=>!o)}><History size={15}/></button>
            {historyOpen&&<><div className="popover-scrim" onClick={()=>setHistoryOpen(false)}/><div className="chat-history-pop">{history}</div></>}</div>
          <button className="btn-quiet !p-1.5" aria-label="New chat" title="New chat" disabled={loading||creating} onClick={()=>{useAppStore.getState().setActiveConversation(null);setError(null);field.current?.focus();}}><SquarePen size={15}/></button>
          {onClose&&<button className="btn-quiet !p-1.5" aria-label="Hide chat" title="Hide chat" onClick={onClose}><PanelRightClose size={15}/></button>}
        </header>}
        <div className={"chat-message-scroll min-h-0 flex-1 overflow-y-auto "+(docked?"px-4 py-5":"px-8 py-8")} role="log" aria-label="Conversation">
          <div className={(docked?"":"mx-auto max-w-3xl ")+"space-y-6"}>
            {loading&&<p role="status" className="text-xs text-zinc-500">Loading chats...</p>}
            {!loading&&!messages.length&&!pendingMessage&&!failure&&<div className="chat-empty">
              <span className="chat-hello" aria-hidden="true"><AgentBot seed="mavo" state="ready"/></span>
              <h2>What can I help with?</h2>
              <div className="suggestions">{SUGGESTIONS.map(text=><button key={text} type="button" className="suggestion" onClick={()=>{useAppStore.setState(st=>({chatInputs:{...st.chatInputs,[key]:text}}));field.current?.focus();}}>{text}</button>)}</div>
            </div>}
            {timeline.map(item=>item.kind==="message"
              ?<ChatMessage key={item.message.id} role={item.message.role} content={item.message.content} names={crewNames} mentions={item.message.mentioned_crew_ids} agentMentions={item.message.mentioned_agent_ids} fresh={item.message.role==="assistant"&&freshReply(item.message.id)}/>
              :item.kind==="task"?<TaskRunCard key={item.run.id} run={item.run}/>
              :<div key="setup" className="space-y-6 chat-setup-inline">{setupCards}</div>)}
            {pendingMessage&&<><ChatMessage role="user" content={pendingMessage+attachedNote} names={crewNames}/>{streaming?<div className="message-content message-assistant streaming whitespace-pre-wrap text-[14px] leading-6 text-zinc-200" aria-live="polite">{streaming}</div>:<Thinking />}</>}
            {failure&&!pendingMessage&&<><ChatMessage role="user" content={failure.message} names={crewNames}/><ChatFailure error={failure.error} problem={failure.problem} requestId={failure.requestId} onRetry={()=>field.current?.form?.requestSubmit()} onSettings={()=>useAppStore.getState().setPage('settings')}/></>}
            <ConversationActivities key={chat?.id??'new'} conversationId={chat?.id}/>
            <div ref={bottom}/>
          </div>
        </div>
        <form onSubmit={submit} className={"chat-composer "+(docked?"px-3 pb-3":"px-8 pb-7")}>
          <div className={docked?"":"mx-auto max-w-3xl"}>
            {(!core?.connected||error||(chatErrors[key]&&!failure))&&<div className="notice mb-3" role="status"><p>{error||core?.error||chatErrors[key]||"Connecting to Mavo…"}</p>{!core?.connected&&<button type="button" className="btn-quiet mt-2" disabled={checking} onClick={()=>void checkConnection()}>{checking?'Checking…':'Try again'}</button>}</div>}
            {refining&&<div className="mb-2 flex items-center justify-between text-xs text-zinc-500"><span>Changing {current?.name}</span><button type="button" className="btn-quiet" onClick={()=>useAppStore.setState(s=>({chatRefinements:{...s.chatRefinements,[key]:""}}))}>Cancel</button></div>}
            <div className="composer relative flex items-end gap-2 p-1.5">
              {mentions.length>0&&<CrewMentionMenu crews={mentions} active={Math.min(mentionAt,mentions.length-1)} onPick={pickMention} onHover={setMentionAt}/>}
              <button type="button" className="btn-quiet !p-2" aria-label="Attach files" title="Attach files (saved in Knowledge)" disabled={attaching||attached.length>=5} onClick={()=>picker.current?.click()}>{attaching?<LoaderCircle size={16} className="animate-spin"/>:<Paperclip size={16}/>}</button>
              <input ref={picker} type="file" multiple hidden accept=".pdf,.docx,.txt,.md,.csv,.json,.png,.jpg,.jpeg" onChange={e=>{void attach(e.target.files);e.target.value="";}}/>
              <div className="mention-field relative min-w-0 flex-1"><div ref={mirror} aria-hidden="true" className="mention-mirror px-2 py-2 text-sm"><MentionText text={input + "\n"} names={crewNames} /></div><textarea ref={field} aria-label="Message Mavo" aria-autocomplete="list" aria-controls={mentions.length?'crew-mention-options':undefined} aria-activedescendant={mentions.length?`crew-mention-${mentions[Math.min(mentionAt,mentions.length-1)].id}`:undefined} maxLength={8000} rows={1} onScroll={e=>{if(mirror.current)mirror.current.scrollTop=e.currentTarget.scrollTop;}} className="mention-input relative block min-h-9 w-full resize-none overflow-y-auto bg-transparent px-2 py-2 text-sm outline-none" value={input} onChange={e=>{const value=e.target.value;setCaret(e.target.selectionStart??value.length);setMentionAt(0);useAppStore.setState(s=>({chatInputs:{...s.chatInputs,[key]:value},chatMentions:{...s.chatMentions,[key]:editMentions(input,value,chosenMentions)}}));}} onSelect={e=>setCaret(e.currentTarget.selectionStart??0)} placeholder="Message Mavo" disabled={sending||creating||loading} onKeyDown={e=>{
                if(mentions.length>0){
                  if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();setMentionAt(i=>(i+(e.key==="ArrowDown"?1:mentions.length-1))%mentions.length);return;}
                  if((e.key==="Enter"&&!e.shiftKey)||e.key==="Tab"){e.preventDefault();pickMention(mentions[Math.min(mentionAt,mentions.length-1)]);return;}
                  if(e.key==="Escape"){e.preventDefault();setMentionClosed(input);return;}
                }
                if(e.key==="Enter"&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();e.currentTarget.form?.requestSubmit();}}}/></div>
              <button aria-label="Send message" className="composer-send" disabled={sending||creating||loading||!input.trim()||!core?.connected}>{sending?<LoaderCircle size={16} className="animate-spin"/>:<ArrowUp size={16}/>}</button>
            </div>
            {!!attached.length&&<div className="attach-chips" aria-label="Attached files">{attached.map(f=><span key={f.id}><Paperclip size={11}/><b title={f.name}>{f.name}</b><button type="button" className="btn-quiet !p-0.5" aria-label={`Remove ${f.name}`} onClick={()=>setAttached(a=>a.filter(x=>x.id!==f.id))}><X size={11}/></button></span>)}</div>}
            {!!chosenMentions.length&&<div className="mention-chips" aria-label="Addressed Crew">{chosenMentions.map(ref=><button type="button" key={`${ref.crewId}:${ref.start}`} style={{...identityStyle(identityColor(`crew:${ref.crewId}`)),color:'var(--identity)',borderColor:'var(--identity)'}} title={`Remove ${crew.find(c=>c.id===ref.crewId)?.name??ref.label}`} onClick={()=>{
              const next=input.slice(0,ref.start)+input.slice(ref.end);
              useAppStore.setState(s=>({chatInputs:{...s.chatInputs,[key]:next},chatMentions:{...s.chatMentions,[key]:editMentions(input,next,chosenMentions)}}));
            }}>{crew.find(c=>c.id===ref.crewId)?.name??ref.label} ×</button>)}</div>}
          </div>
        </form>
      </section>
      {setupCards&&<aside className="chat-side" aria-label="Agents in this chat"><p className="chat-side-head">{shown.length>1?"Agents in this chat":"Agent in this chat"}</p><div className="space-y-4">{setupCards}</div></aside>}
    </div>
    {dialog&&<ChatDialog mode={dialog.mode} chat={dialog.chat} title={title} busy={dialogBusy} error={dialogError} onTitle={setTitle} onCancel={()=>setDialog(null)} onConfirm={()=>void confirmDialog()}/>}
  </div>;
}

function ChatMessage({ role, content, names = [],mentions=[],agentMentions=[],fresh=false }: { role: "user" | "assistant"; content: string; label?: string; names?: string[];mentions?:string[];agentMentions?:string[];fresh?:boolean }) {
  const crews=useAppStore(s=>s.crew);
  const members=crews.flatMap(c=>(c.agents??[]).map(a=>({id:a.id,crewId:c.id,label:`${c.name}/${a.name}`})));
  if (role === "user") return <div className="flex flex-col items-end gap-1"><div className="message-content message-user max-w-[80%] whitespace-pre-wrap text-[14px] leading-6"><MentionText text={typedQuestion(content)} names={names} /></div>{!!(mentions.length||agentMentions.length)&&<div className="mention-chips">{mentions.map(id=><button type="button" key={id} style={{...identityStyle(identityColor(`crew:${id}`)),color:'var(--identity)'}} disabled={!crews.some(c=>c.id===id)} onClick={()=>useAppStore.getState().openAgent(id)}>{crews.find(c=>c.id===id)?.name??'Deleted Crew'}</button>)}{agentMentions.map(id=>{const member=members.find(a=>a.id===id);return <button key={`member:${id}`} type="button" style={{...identityStyle(identityColor(`member:${id}`)),color:'var(--identity)'}} disabled={!member} onClick={()=>member&&useAppStore.getState().openAgent(member.crewId)}>{member?.label??`Removed agent ${id.slice(0,6)}`}</button>;})}</div>}</div>;
  return <div className="message-content message-assistant whitespace-pre-wrap text-[14px] leading-6 text-zinc-200"><TypedText text={content} animate={fresh} /></div>;
}

const EMPTY_MENTIONS:import('../lib/orchestration').MentionRef[]=[];

export function Thinking() {
  return <div role="status" className="thinking" aria-label="Thinking"><span className="thinking-bot"><AgentBot seed="mavo" state="working"/></span><span className="thinking-dots"><i/><i/><i/></span></div>;
}
