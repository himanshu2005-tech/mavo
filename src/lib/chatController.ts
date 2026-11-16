import { api, ApiError } from "./api";
import { useAppStore } from "../store/app";
import type { Conversation } from "../types";

// Keep in-flight UI state across page navigation, but never across account logout.
export async function sendCoreMessage(chat:Conversation,message:string,crewId?:string,mentionedCrewIds?:string[],mentionedAgentIds?:string[],attachments?:string[]) {
  const state=useAppStore.getState();
  if(state.pendingChatMessages[chat.id])return;
  const epoch=state.workspaceEpoch;
  const requestId=crypto.randomUUID();
  useAppStore.setState(s=>({chatFailures:Object.fromEntries(Object.entries(s.chatFailures).filter(([id])=>id!==chat.id))}));
  useAppStore.setState(s=>({pendingChatMessages:{...s.pendingChatMessages,[chat.id]:message},chatInputs:{...s.chatInputs,[chat.id]:""},chatErrors:{...s.chatErrors,[chat.id]:""}}));
  try {
    // The reply appears as Mavo writes it; the saved reply replaces it when the turn is done.
    const onReply=(text:string)=>{if(useAppStore.getState().workspaceEpoch===epoch)useAppStore.setState(s=>({streamingReplies:{...s.streamingReplies,[chat.id]:text}}));};
    const result=await (api.coordinatorTurnStream
      ?api.coordinatorTurnStream(onReply,message,chat.session_id??"main",crewId,requestId,chat.id,mentionedCrewIds,mentionedAgentIds,attachments)
      :mentionedAgentIds?.length||attachments?.length
      ?api.coordinatorTurn(message,chat.session_id??"main",crewId,requestId,chat.id,mentionedCrewIds,mentionedAgentIds,attachments)
      :api.coordinatorTurn(message,chat.session_id??"main",crewId,requestId,chat.id,mentionedCrewIds));
    if(useAppStore.getState().workspaceEpoch!==epoch||!useAppStore.getState().conversations.some(c=>c.id===chat.id))return;
    const current=useAppStore.getState();
    if(result.conversation)current.upsertConversation(result.conversation,false);
    const setups=result.crews?.length?result.crews:[result.crew??result.draft].filter((c):c is NonNullable<typeof c>=>!!c);
    for(const setup of setups)current.upsertCrew(setup);
    useAppStore.setState(s=>({chatMentions:{...s.chatMentions,[chat.id]:[]}}));
    useAppStore.setState(s=>({chatRefinements:{...s.chatRefinements,[chat.id]:""}}));
  } catch(error) {
    if(useAppStore.getState().workspaceEpoch!==epoch)return;
    const explanation=error instanceof Error?error.message:'Mavo encountered an unexpected error. Your message is kept below.';
    useAppStore.setState(s=>s.workspaceEpoch!==epoch||!s.conversations.some(c=>c.id===chat.id)?{}:{chatInputs:{...s.chatInputs,[chat.id]:s.chatInputs[chat.id]||message},chatErrors:{...s.chatErrors,[chat.id]:explanation},chatFailures:{...s.chatFailures,[chat.id]:{message,error:explanation,requestId,problem:error instanceof ApiError?error.problem:undefined}}});
  } finally {
    if(useAppStore.getState().workspaceEpoch===epoch)useAppStore.setState(s=>{
      const pending={...s.pendingChatMessages};delete pending[chat.id];
      const streaming={...s.streamingReplies};delete streaming[chat.id];
      return {pendingChatMessages:pending,streamingReplies:streaming};
    });
  }
}
