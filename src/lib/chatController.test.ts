import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { sendCoreMessage } from "./chatController";
import { clearWorkspace, useAppStore } from "../store/app";
import type { Conversation, CoordinatorResponse } from "../types";

vi.mock("./api",()=>({api:{coordinatorTurn:vi.fn()}}));
const first:Conversation={id:"first",session_id:"first-session",title:"First",created_at:"now",updated_at:"now",messages:[]};
const second:Conversation={...first,id:"second",session_id:"second-session",title:"Second"};
const reply:CoordinatorResponse={conversation:{...first,title:"Updated first"},reply:"Hello",request_id:"request",session_id:"first-session",model:"fixture",questions:[],crew:null,draft:null,messages:[]};

describe("independent chat requests",()=>{
  beforeEach(()=>{
    vi.resetAllMocks();clearWorkspace();useAppStore.getState().setConversations([first,second]);
  });
  it("keeps a late response in its original chat without switching the selected chat",async()=>{
    let finish!:(response:CoordinatorResponse)=>void;
    vi.mocked(api.coordinatorTurn).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
    const pending=sendCoreMessage(first,"Hello");
    expect(useAppStore.getState().pendingChatMessages.first).toBe("Hello");
    useAppStore.getState().setActiveConversation(second.id);
    finish(reply);await pending;
    expect(api.coordinatorTurn).toHaveBeenCalledWith("Hello","first-session",undefined,expect.any(String),"first");
    expect(useAppStore.getState().activeConversationId).toBe("second");
    expect(useAppStore.getState().conversations.find(c=>c.id==="first")?.title).toBe("Updated first");
    expect(useAppStore.getState().pendingChatMessages).toEqual({});
  });
  it("does not restore messages or setup after logout during an in-flight request",async()=>{
    let finish!:(response:CoordinatorResponse)=>void;
    vi.mocked(api.coordinatorTurn).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
    const pending=sendCoreMessage(first,"Private message");
    clearWorkspace();finish(reply);await pending;
    expect(useAppStore.getState().conversations).toEqual([]);
    expect(useAppStore.getState().pendingChatMessages).toEqual({});
    expect(useAppStore.getState().chatInputs).toEqual({});
  });
  it("restores a failed message only to the chat that sent it",async()=>{
    vi.mocked(api.coordinatorTurn).mockRejectedValue(Error("Connection lost"));
    useAppStore.setState({chatInputs:{second:"Unsent second chat text"}});
    await sendCoreMessage(first,"Retry me");
    expect(useAppStore.getState().chatInputs).toEqual({first:"Retry me",second:"Unsent second chat text"});
    expect(useAppStore.getState().chatErrors).toEqual({first:"Connection lost"});
  });
  it("blocks duplicate concurrent sends in one chat",async()=>{
    let finish!:(response:CoordinatorResponse)=>void;
    vi.mocked(api.coordinatorTurn).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
    const pending=sendCoreMessage(first,"Hello");
    await sendCoreMessage(first,"Duplicate");
    expect(api.coordinatorTurn).toHaveBeenCalledTimes(1);
    finish(reply);await pending;
  });
});
