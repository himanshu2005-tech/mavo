import { LoaderCircle, MessageCircle, Pencil, Plus, Trash2 } from "lucide-react";
import type { Conversation } from "../types";

export function ChatHistory({chats,activeId,pending,disabled,onNew,onSelect,onRename,onDelete}:{chats:Conversation[];activeId:string|null;pending:Record<string,string>;disabled:boolean;onNew:()=>void;onSelect:(id:string)=>void;onRename:(chat:Conversation)=>void;onDelete:(chat:Conversation)=>void}) {
  return <nav className="chat-history" aria-label="Chats">
    <button className="btn-secondary w-full" disabled={disabled} onClick={onNew}><Plus size={15}/>New chat</button>
    <p className="mt-6 mb-1 px-2 text-[12px] font-semibold text-zinc-500">Chats</p>
    <div className="chat-history-list">{!chats.length&&<p className="px-2 py-3 text-xs leading-5 text-zinc-500">Your conversations will appear here.</p>}{chats.map(chat=><div className="chat-history-row" key={chat.id} data-chat-id={chat.id} data-active={chat.id===activeId}>
      <button className="chat-history-select" aria-current={chat.id===activeId?"page":undefined} onClick={()=>onSelect(chat.id)} title={chat.title}>
        {pending[chat.id]?<LoaderCircle size={13} className="shrink-0 animate-spin"/>:<MessageCircle size={13} className="shrink-0"/>}<span className="truncate">{chat.title}</span>
      </button>
      <div className="chat-history-tools"><button className="btn-quiet !p-1" aria-label={`Rename ${chat.title}`} onClick={()=>onRename(chat)}><Pencil size={12}/></button><button className="btn-quiet !p-1" aria-label={`Delete ${chat.title}`} disabled={!!pending[chat.id]} onClick={()=>onDelete(chat)}><Trash2 size={12}/></button></div>
    </div>)}</div>
  </nav>;
}

export function ChatDialog({mode,chat,title,busy,error,onTitle,onCancel,onConfirm}:{mode:"rename"|"delete";chat:Conversation;title:string;busy:boolean;error:string|null;onTitle:(value:string)=>void;onCancel:()=>void;onConfirm:()=>void}) {
  return <div className="sheet-backdrop"><form className="sheet !max-w-md" role="dialog" aria-modal="true" aria-labelledby="chat-dialog-title" onSubmit={event=>{event.preventDefault();onConfirm();}} onKeyDown={event=>{if(event.key==="Escape"&&!busy)onCancel();}}>
    <h2 id="chat-dialog-title" className="text-lg font-semibold">{mode==="rename"?"Rename chat":"Delete chat?"}</h2>
    {mode==="rename"?<label className="mt-4 block text-xs text-zinc-400">Name<input autoFocus className="form-control" value={title} maxLength={160} onChange={event=>onTitle(event.target.value)} disabled={busy}/></label>:<p className="mt-3 text-sm leading-6 text-zinc-400">Delete “{chat.title}” and its messages? This cannot be undone. Your saved Crew and their permissions will not be deleted.</p>}
    {error&&<p role="alert" className="notice error-notice mt-4">{error}</p>}
    <div className="mt-5 flex justify-end gap-2"><button autoFocus={mode==="delete"} className="btn-quiet" type="button" disabled={busy} onClick={onCancel}>Cancel</button><button className={`btn-primary ${mode==="delete"?"btn-stop":""}`} disabled={busy||(mode==="rename"&&!title.trim())}>{busy&&<LoaderCircle size={13} className="animate-spin"/>}{mode==="rename"?"Save":"Delete chat"}</button></div>
  </form></div>;
}
