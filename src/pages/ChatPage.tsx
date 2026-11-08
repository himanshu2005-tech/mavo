import { AlarmClock, ArrowUp, ArrowUpRight, Bot, Github, Globe, History, KeyRound, LoaderCircle, PanelRightClose, Plane, Plus } from "lucide-react";
import { CrewArt, HUES } from "../components/CrewArt";
import { IconTile } from "../components/Studio";
import { SaveLoginForm } from "../components/SaveLogin";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ChatDialog, ChatHistory } from "../components/ChatHistory";
import { CrewSetupCard } from "../components/CrewSetupCard";
import { TaskRunCard, chatTimeline, useConversationTasks } from "../components/TaskRunCard";
import { CrewMentionMenu, MentionText, insertMention, mentionMatches, mentionQuery } from "../components/CrewMentions";
import { CrewGraph } from "../components/CrewGraph";
import { api } from "../lib/api";
import { sendCoreMessage } from "../lib/chatController";
import { useAppStore } from "../store/app";
import type { Conversation, CoreStatus, CrewInstance } from "../types";

const SUGGESTIONS = [
  { text: "Make an agent that summarises my important mail every morning", icon: Bot, hue: 286 },
  { text: "Remind me to have breakfast at 9, lunch at 1 and dinner at 7:45", icon: AlarmClock, hue: 32 },
  { text: "I'm flying Hyderabad to Chennai on Friday. Help me prepare", icon: Plane, hue: 202 },
  { text: "Tell me when CI fails on my GitHub repo", icon: Github, hue: 268 },
  { text: "Search the web for the cheapest train fares to Chennai", icon: Globe, hue: 176 }
];

/** Mavo's chat. Docked, it sits beside the graph: narrower, with its history in a popover. */
export function ChatPage({ docked = false, onClose }: { docked?: boolean; onClose?: () => void } = {}) {
  const { conversations,activeConversationId,crew,setupDrafts,chatInputs,pendingChatMessages,chatErrors,chatRefinements }=useAppStore();
  const [core,setCore]=useState<CoreStatus|null>(null);
  const [loading,setLoading]=useState(true);
  const [creating,setCreating]=useState(false);
  const [loginOpen,setLoginOpen]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [dialog,setDialog]=useState<{mode:"rename"|"delete";chat:Conversation}|null>(null);
  const [historyOpen,setHistoryOpen]=useState(false);
  const refineRequest=useAppStore(s=>s.refineRequest);
  const chatFocus=useAppStore(s=>s.chatFocus);
  const [title,setTitle]=useState("");
  const [dialogBusy,setDialogBusy]=useState(false);
  const [dialogError,setDialogError]=useState<string|null>(null);
  const creatingLock=useRef(false);
  const active=useRef(true);
  const bottom=useRef<HTMLDivElement>(null);
  const field=useRef<HTMLTextAreaElement>(null);
  const chat=conversations.find(c=>c.id===activeConversationId);
  const key=chat?.id??"new";
  const input=chatInputs[key]??"";
  const pendingMessage=pendingChatMessages[key];
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
  const crewNames=crew.filter(c=>c.status!=="draft").map(c=>c.name);
  const mirror=useRef<HTMLDivElement>(null);
  const mentions=mention&&mentionClosed!==input?mentionMatches(crew,mention.query):[];
  function pickMention(chosen:CrewInstance){
    if(!mention)return;
    const next=insertMention(input,mention.start,caret,chosen.name);
    useAppStore.setState(s=>({chatInputs:{...s.chatInputs,[key]:next.text}}));
    setCaret(next.caret);setMentionAt(0);
    requestAnimationFrame(()=>{field.current?.focus();field.current?.setSelectionRange(next.caret,next.caret);});
  }
  const current=everyCrew.find(c=>c.id===(refining??chat?.planning_crew_id));
  const batch=refining?[]:(chat?.planning_crew_ids??[]).map(id=>everyCrew.find(c=>c.id===id)).filter((c):c is NonNullable<typeof c>=>!!c);
  const shown=batch.length>1?batch:current?[current]:[];
  const links=shown.flatMap(c=>(c.configuration_plan?.upstream??[]).filter(id=>shown.some(s=>s.id===id)).map(id=>({from:id,to:c.id})));
  const timeline=chatTimeline(messages,runs,shown);
  useEffect(()=>{
    active.current=true;
    let cancelled=false;
    const epoch=useAppStore.getState().workspaceEpoch;
    const originalHistory=useAppStore.getState().conversations;
    const valid=()=>!cancelled&&active.current&&useAppStore.getState().workspaceEpoch===epoch;
    void api.coreStatus().then(status=>{if(valid())setCore(status);}).catch(e=>{if(valid())setError(String(e));});
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
  useEffect(()=>{bottom.current?.scrollIntoView({behavior:"smooth"});},[activeConversationId,messages.length,pendingMessage,current?.id]);
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
    if(!input.trim()||sending||creating||loading||!core?.connected)return;
    const message=input.trim();
    const target=chat??await newChat();
    if(!target)return;
    if(!chat)useAppStore.setState(s=>({chatInputs:{...s.chatInputs,new:""}}));
    await sendCoreMessage(target,message,refining);
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
  const history=<ChatHistory chats={conversations} activeId={activeConversationId} pending={pendingChatMessages} disabled={loading||creating} onNew={()=>{setHistoryOpen(false);void newChat();}} onSelect={id=>{useAppStore.getState().setActiveConversation(id);setError(null);setHistoryOpen(false);}} onRename={item=>openDialog("rename",item)} onDelete={item=>openDialog("delete",item)}/>;
  return <div className={docked?"chat-dock-inner":"flex h-screen min-h-0 flex-col"} data-docked={docked}>
    <div className="chat-workspace">
      {!docked&&history}
      <section className="flex min-w-0 flex-1 flex-col" aria-label={chat?.title??"New chat"}>
  {docked?<header className="chat-dock-bar" aria-label="Assistant">
          <div className="relative"><button className="btn-quiet !p-1.5" aria-label="Chats" title="Chats" aria-expanded={historyOpen} onClick={()=>setHistoryOpen(o=>!o)}><History size={15}/></button>
            {historyOpen&&<><div className="popover-scrim" onClick={()=>setHistoryOpen(false)}/><div className="chat-history-pop">{history}</div></>}</div>
          <h1 className="min-w-0 flex-1 truncate text-[13.5px] font-semibold text-zinc-50">{chat?.title??"New chat"}</h1>
          <button className="btn-quiet !p-1.5" aria-label="New chat" title="New chat" disabled={loading||creating} onClick={()=>{useAppStore.getState().setActiveConversation(null);setError(null);field.current?.focus();}}><Plus size={15}/></button>
          {onClose&&<button className="btn-quiet !p-1.5" aria-label="Hide chat" title="Hide chat" onClick={onClose}><PanelRightClose size={15}/></button>}
        </header>
        :<header className="chat-bar" aria-label="Assistant"><h1 className="truncate text-[15px] font-semibold tracking-[-0.01em] text-zinc-50">{chat?.title??"New chat"}</h1></header>}
        <div className={"chat-message-scroll min-h-0 flex-1 overflow-y-auto "+(docked?"px-4 py-5":"px-8 py-8")} role="log" aria-label="Conversation">
          <div className={(docked?"":"mx-auto max-w-3xl ")+"space-y-6"}>
            {loading&&<p role="status" className="text-xs text-zinc-500">Loading chats...</p>}
            {!loading&&!messages.length&&!pendingMessage&&<div className="chat-empty">{!docked&&<div className="tile-art" style={{["--hue" as string]:HUES.communication,width:300,height:140,borderRadius:32,margin:"0 auto 22px"}} aria-hidden="true"><CrewArt template="communication"/></div>}<h2 className={docked?"text-[16px] font-semibold tracking-[-0.01em] text-zinc-50":"text-[28px] font-semibold tracking-[-0.025em] text-zinc-50"}>What can I help with?</h2><p className={docked?"mx-auto mt-1.5 max-w-xs text-[12.5px] leading-5 text-zinc-500":"mx-auto mt-2 max-w-md text-[14px] leading-6 text-zinc-500"}>{docked?"Ask anything, or describe an agent to make.":"Ask anything, or describe something to handle."}</p><div className="suggestions">{SUGGESTIONS.slice(0,docked?3:SUGGESTIONS.length).map(item=><button key={item.text} type="button" className="suggestion lift" onClick={()=>{useAppStore.setState(st=>({chatInputs:{...st.chatInputs,[key]:item.text}}));field.current?.focus();}}>{!docked&&<IconTile icon={item.icon} hue={item.hue} size={38}/>}<span className="min-w-0 flex-1 self-center">{item.text}</span><ArrowUpRight size={15} className="arrow self-center"/></button>)}</div></div>}
            {timeline.map(item=>item.kind==="message"
              ?<ChatMessage key={item.message.id} role={item.message.role} content={item.message.content} names={crewNames}/>
              :item.kind==="task"?<TaskRunCard key={item.run.id} run={item.run}/>
              :<div key="setup" className="space-y-6">
                {links.length>0&&<CrewGraph nodes={item.crews.map(c=>({id:c.id,name:c.name,state:c.runtime_state,running:c.is_running,status:c.status}))} edges={links}/>}
                {item.crews.map(c=><CrewSetupCard key={c.id+JSON.stringify(c.configuration_plan)} crew={c} onRefine={refine}/>)}
              </div>)}
            {pendingMessage&&<><ChatMessage role="user" content={pendingMessage} names={crewNames}/><ThinkingStages /></>}
            <div ref={bottom}/>
          </div>
        </div>
        <form onSubmit={submit} className={"chat-composer "+(docked?"px-3 pb-3":"px-8 pb-7")}>
          <div className={docked?"":"mx-auto max-w-3xl"}>
            {(!core?.connected||error||chatErrors[key])&&<p className="notice mb-3" role="status">{chatErrors[key]||error||core?.error||"Connecting to Mavo Core..."}</p>}
            {refining&&<div className="mb-2 flex items-center justify-between text-xs text-zinc-500"><span>Refining {current?.name}</span><button type="button" className="btn-quiet" onClick={()=>useAppStore.setState(s=>({chatRefinements:{...s.chatRefinements,[key]:""}}))}>Cancel</button></div>}
            {loginOpen&&<div className="surface mb-3 p-5"><div className="mb-3 flex items-center justify-between"><h3 className="text-[14px] font-semibold">Save a login</h3><button type="button" className="btn-quiet !py-1" onClick={()=>setLoginOpen(false)}>Close</button></div><SaveLoginForm compact/></div>}
            <div className="composer relative flex items-end gap-3 p-2.5">
              {mentions.length>0&&<CrewMentionMenu crews={mentions} active={Math.min(mentionAt,mentions.length-1)} onPick={pickMention} onHover={setMentionAt}/>}
              <button type="button" className="btn-quiet !p-2.5" aria-label="Save a login" title="Save a website login" aria-pressed={loginOpen} onClick={()=>setLoginOpen(!loginOpen)}><KeyRound size={17}/></button>
              <div className="mention-field relative min-w-0 flex-1"><div ref={mirror} aria-hidden="true" className="mention-mirror px-2 py-2 text-sm"><MentionText text={input + "\n"} names={crewNames} /></div><textarea ref={field} aria-label="Message Mavo" maxLength={8000} rows={2} onScroll={e=>{if(mirror.current)mirror.current.scrollTop=e.currentTarget.scrollTop;}} className="mention-input relative block min-h-10 w-full resize-none bg-transparent px-2 py-2 text-sm outline-none" value={input} onChange={e=>{const value=e.target.value;setCaret(e.target.selectionStart??value.length);setMentionAt(0);useAppStore.setState(s=>({chatInputs:{...s.chatInputs,[key]:value}}));}} onSelect={e=>setCaret(e.currentTarget.selectionStart??0)} placeholder="Tell Mavo what you need..." disabled={sending||creating||loading} onKeyDown={e=>{
                if(mentions.length>0){
                  if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();setMentionAt(i=>(i+(e.key==="ArrowDown"?1:mentions.length-1))%mentions.length);return;}
                  if((e.key==="Enter"&&!e.shiftKey)||e.key==="Tab"){e.preventDefault();pickMention(mentions[Math.min(mentionAt,mentions.length-1)]);return;}
                  if(e.key==="Escape"){e.preventDefault();setMentionClosed(input);return;}
                }
                if(e.key==="Enter"&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();e.currentTarget.form?.requestSubmit();}}}/></div>
              <button aria-label="Send message" className="btn-primary !p-2.5" disabled={sending||creating||loading||!input.trim()||!core?.connected}>{sending?<LoaderCircle size={17} className="animate-spin"/>:<ArrowUp size={17}/>}</button>
            </div>
          </div>
        </form>
      </section>
    </div>
    {dialog&&<ChatDialog mode={dialog.mode} chat={dialog.chat} title={title} busy={dialogBusy} error={dialogError} onTitle={setTitle} onCancel={()=>setDialog(null)} onConfirm={()=>void confirmDialog()}/>}
  </div>;
}

function ChatMessage({ role, content, names = [] }: { role: "user" | "assistant"; content: string; label?: string; names?: string[] }) {
  if (role === "user") return <div className="flex justify-end"><div className="message-content message-user max-w-[80%] whitespace-pre-wrap text-[14px] leading-6"><MentionText text={content} names={names} /></div></div>;
  return <div className="flex items-start gap-3"><span className="avatar" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M4.5 19V6.2c0-.5.6-.7.9-.3L12 14.4l6.6-8.5c.3-.4.9-.2.9.3V19" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" /></svg></span><div className="message-content message-assistant max-w-[92%] whitespace-pre-wrap text-[14px] leading-6 text-zinc-200">{content}</div></div>;
}

function TypingDots() {
  return <span className="flex items-center gap-1" aria-label="Loading"><span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-500" /><span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-500 [animation-delay:120ms]" /><span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-500 [animation-delay:240ms]" /></span>;
}


const STAGES = ["Reading your request", "Choosing the right Crew", "Checking what is available", "Preparing your setup"];

/** Honest progress cue while the planner works: it advances on a timer and holds on the last step. */
function ThinkingStages() {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setStage(s => Math.min(s + 1, STAGES.length - 1)), 1800);
    return () => window.clearInterval(timer);
  }, []);
  return <div role="status" className="stages"><span className="stages-orb" aria-hidden="true" /><span key={stage} className="stages-text">{STAGES[stage]}…</span></div>;
}
