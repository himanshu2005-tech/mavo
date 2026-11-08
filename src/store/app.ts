import { create } from "zustand";
import {mergeExecution,type ExecutionFeed,type RuntimeSnapshot} from '../lib/executionSignals';
import type { CrewProposal } from "../types";
import type { AppStatus, Conversation, CrewInstance, CrewTemplate, ModelRoute, NavId } from "../types";

interface AppState {
  execution:ExecutionFeed|null;
  executionError:string|null;
  setExecution:(snapshot:RuntimeSnapshot)=>void;
  setExecutionError:(error:string|null)=>void;
  workspaceEpoch: number;
  chatInputs: Record<string,string>;
  pendingChatMessages: Record<string,string>;
  chatErrors: Record<string,string>;
  chatRefinements: Record<string,string>;
  crewProposals: CrewProposal[];
  setCrewProposals: (proposals: CrewProposal[]) => void;
  page: NavId;
  status: AppStatus | null;
  statusError: string | null;
  conversations: Conversation[];
  crew: CrewInstance[];
  setupDrafts: CrewInstance[];
  deletedCrewIds: string[];
  setSetupDrafts: (drafts: CrewInstance[]) => void;
  crewTemplates: CrewTemplate[];
  routes: ModelRoute[];
  activeConversationId: string | null;
  selectedRouteId: string;
  /** Where you are: home (the graph) or one of the sheets over it. "chat" and "crew" are the old
   *  pages' names: both land on home, "chat" with the chat open. */
  setPage: (page: NavId | "chat" | "crew") => void;
  chatOpen: boolean;
  setChatOpen: (open: boolean) => void;
  /** Which side of the chat: your agents and Mavo talking with you, or building agents. */
  chatTab: "chat" | "build";
  setChatTab: (tab: "chat" | "build") => void;
  /** An agent the graph should select and show (from a desktop card, a chat card, ...). */
  focusAgent: string | null;
  openAgent: (id: string | null) => void;
  /** A draft to keep shaping in chat. */
  refineRequest: string | null;
  refineInChat: (id: string | null) => void;
  /** Bumped to put the cursor in the chat box. */
  chatFocus: number;
  startInChat: (text: string) => void;
  setStatus: (status: AppStatus) => void;
  setStatusError: (error: string | null) => void;
  setConversations: (conversations: Conversation[]) => void;
  setCrew: (crew: CrewInstance[]) => void;
  setCrewTemplates: (templates: CrewTemplate[]) => void;
  setRoutes: (routes: ModelRoute[]) => void;
  upsertRoute: (route: ModelRoute) => void;
  removeRoute: (id: string) => void;
  upsertCrew: (crew: CrewInstance) => void;
  removeCrew: (id: string) => void;
  setActiveConversation: (id: string | null) => void;
  setSelectedRoute: (id: string) => void;
  upsertConversation: (conversation: Conversation, activate?: boolean) => void;
  removeConversation: (id: string) => void;
}

export function clearWorkspace() {
  useAppStore.setState(state=>({execution:null,executionError:null,workspaceEpoch:state.workspaceEpoch+1,chatInputs:{},pendingChatMessages:{},chatErrors:{},chatRefinements:{},conversations: [], crew: [], setupDrafts: [], deletedCrewIds: [], crewProposals: [], routes: [], status: null, statusError: null, activeConversationId: null, selectedRouteId: "", page: "home", focusAgent: null, refineRequest: null}));
}

export const useAppStore = create<AppState>((set) => ({
  execution:null,executionError:null,
  setExecution:snapshot=>set(state=>({execution:mergeExecution(state.execution,snapshot),executionError:null})),
  setExecutionError:executionError=>set({executionError}),
  workspaceEpoch: 0,
  chatInputs: {},
  pendingChatMessages: {},
  chatErrors: {},
  chatRefinements: {},
  crewProposals: [],
  setCrewProposals: (crewProposals) => set({ crewProposals }),
  page: "home",
  chatOpen: (() => { try { return localStorage.getItem("mavo-chat-open") !== "0"; } catch { return true; } })(),
  setChatOpen: (chatOpen) => { try { localStorage.setItem("mavo-chat-open", chatOpen ? "1" : "0"); } catch { /* fine */ } set({ chatOpen }); },
  chatTab: (() => { try { return localStorage.getItem("mavo-chat-tab") === "build" ? "build" : "chat"; } catch { return "chat"; } })(),
  setChatTab: (chatTab) => { try { localStorage.setItem("mavo-chat-tab", chatTab); } catch { /* fine */ } set({ chatTab }); },
  focusAgent: null,
  openAgent: (focusAgent) => set({ page: "home", focusAgent }),
  refineRequest: null,
  refineInChat: (refineRequest) => { try { localStorage.setItem("mavo-chat-open", "1"); } catch { /* fine */ } set({ refineRequest, page: "home", chatOpen: true, chatTab: "build" }); },
  chatFocus: 0,
  startInChat: (text) => { try { localStorage.setItem("mavo-chat-open", "1"); } catch { /* fine */ } set(state => ({ page: "home", chatOpen: true, chatTab: "build", chatFocus: state.chatFocus + 1, activeConversationId: null, chatInputs: { ...state.chatInputs, new: text } })); },
  status: null,
  statusError: null,
  conversations: [],
  crew: [],
  setupDrafts: [],
  deletedCrewIds: [],
  setSetupDrafts: (drafts) => set(state=>({setupDrafts: drafts.filter(d=>d.status==="draft"&&!state.deletedCrewIds.includes(d.id)&&!state.crew.some(c=>c.id===d.id))})),
  crewTemplates: [],
  routes: [],
  activeConversationId: null,
  selectedRouteId: "",
  setPage: (page) => {
    if (page === "chat") { try { localStorage.setItem("mavo-chat-open", "1"); } catch { /* fine */ } set({ page: "home", chatOpen: true }); }
    else set({ page: page === "crew" ? "home" : page });
  },
  setStatus: (status) => set({ status, statusError: null }),
  setStatusError: (statusError) => set({ statusError }),
  setConversations: (conversations) => set(state=>({conversations, activeConversationId:conversations.some(c=>c.id===state.activeConversationId)?state.activeConversationId:conversations[0]?.id??null})),
  setCrew: (crew) => set(state=>({crew: crew.filter(c=>c.status!=="draft"&&!state.deletedCrewIds.includes(c.id))})),
  setCrewTemplates: (crewTemplates) => set({ crewTemplates }),
  setRoutes: (routes) => set((state) => ({ routes, selectedRouteId: routes.some((route) => route.id === state.selectedRouteId) ? state.selectedRouteId : routes[0]?.id ?? "" })),
  upsertRoute: (route) => set((state) => ({ routes: [route, ...state.routes.filter((item) => item.id !== route.id)], selectedRouteId: state.selectedRouteId || route.id })),
  removeRoute: (id) => set((state) => { const routes = state.routes.filter((item) => item.id !== id); return { routes, selectedRouteId: state.selectedRouteId === id ? routes[0]?.id ?? "" : state.selectedRouteId }; }),
  upsertCrew: (item) => set((state) => state.deletedCrewIds.includes(item.id) ? {} : item.status === "draft"
    ? {setupDrafts: [item, ...state.setupDrafts.filter(d=>d.id!==item.id)], crew:state.crew.filter(c=>c.id!==item.id)}
    : {crew: [item, ...state.crew.filter(c=>c.id!==item.id)], setupDrafts:state.setupDrafts.filter(d=>d.id!==item.id)}),
  removeCrew: (id) => set((state) => ({ crew: state.crew.filter((item) => item.id !== id), setupDrafts:state.setupDrafts.filter(d=>d.id!==id), deletedCrewIds:[...new Set([...state.deletedCrewIds,id])] })),
  setActiveConversation: (activeConversationId) => set({ activeConversationId }),
  setSelectedRoute: (selectedRouteId) => set({ selectedRouteId }),
  removeConversation: (id) => set(state=>{
    const conversations=state.conversations.filter(c=>c.id!==id);
    const without=(values:Record<string,string>)=>Object.fromEntries(Object.entries(values).filter(([key])=>key!==id));
    return {conversations,activeConversationId:state.activeConversationId===id?conversations[0]?.id??null:state.activeConversationId,chatInputs:without(state.chatInputs),pendingChatMessages:without(state.pendingChatMessages),chatErrors:without(state.chatErrors),chatRefinements:without(state.chatRefinements)};
  }),
  upsertConversation: (conversation, activate=true) =>
    set((state) => ({
      conversations: [conversation, ...state.conversations.filter((item) => item.id !== conversation.id)],
      activeConversationId: activate?conversation.id:state.activeConversationId
    }))
}));
