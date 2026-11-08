import { api } from "./api";
import { useAppStore } from "../store/app";
import type { Conversation } from "../types";

// Keep in-flight UI state across page navigation, but never across account logout.
export async function sendCoreMessage(chat:Conversation,message:string,crewId?:string) {
  const state=useAppStore.getState();
  if(state.pendingChatMessages[chat.id])return;
  const epoch=state.workspaceEpoch;
  useAppStore.setState(s=>({pendingChatMessages:{...s.pendingChatMessages,[chat.id]:message},chatInputs:{...s.chatInputs,[chat.id]:""},chatErrors:{...s.chatErrors,[chat.id]:""}}));
  try {
    const result=await api.coordinatorTurn(message,chat.session_id??"main",crewId,crypto.randomUUID(),chat.id);
    if(useAppStore.getState().workspaceEpoch!==epoch)return;
    const current=useAppStore.getState();
    if(result.conversation)current.upsertConversation(result.conversation,false);
    const setups=result.crews?.length?result.crews:[result.crew??result.draft].filter((c):c is NonNullable<typeof c>=>!!c);
    for(const setup of setups)current.upsertCrew(setup);
    useAppStore.setState(s=>({chatRefinements:{...s.chatRefinements,[chat.id]:""}}));
  } catch(error) {
    if(useAppStore.getState().workspaceEpoch!==epoch)return;
    useAppStore.setState(s=>({chatInputs:{...s.chatInputs,[chat.id]:s.chatInputs[chat.id]||message},chatErrors:{...s.chatErrors,[chat.id]:error instanceof Error?error.message:"Mavo could not finish this message."}}));
  } finally {
    if(useAppStore.getState().workspaceEpoch===epoch)useAppStore.setState(s=>{
      const pending={...s.pendingChatMessages};delete pending[chat.id];return {pendingChatMessages:pending};
    });
  }
}
