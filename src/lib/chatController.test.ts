import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";
import { sendCoreMessage } from "./chatController";
import { clearWorkspace, useAppStore } from "../store/app";
import type { Conversation, CoordinatorResponse } from "../types";

vi.mock("./api",async(importOriginal)=>({...await importOriginal<typeof import('./api')>(),api:{coordinatorTurn:vi.fn()}}));
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
    expect(api.coordinatorTurn).toHaveBeenCalledWith("Hello","first-session",undefined,expect.any(String),"first",undefined);
    expect(useAppStore.getState().activeConversationId).toBe("second");
    expect(useAppStore.getState().conversations.find(c=>c.id==="first")?.title).toBe("Updated first");
    expect(useAppStore.getState().pendingChatMessages).toEqual({});
  });
  it("sends the files attached to a message with it",async()=>{
    vi.mocked(api.coordinatorTurn).mockResolvedValue(reply);
    await sendCoreMessage(first,"Use my resume",undefined,[],[],["file-1"]);
    expect(api.coordinatorTurn).toHaveBeenCalledWith("Use my resume","first-session",undefined,expect.any(String),"first",[],[],["file-1"]);
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
  it("keeps the failed message, precise cause and request reference in its original chat",async()=>{
    const problem={code:'core_provider_credits',message:'Core has insufficient credits.',action:'The publisher must top up Core.',retryable:false,request_id:'reference'};
    vi.mocked(api.coordinatorTurn).mockRejectedValue(new ApiError(problem.message,503,problem));
    await sendCoreMessage(first,'Please plan my day');
    expect(useAppStore.getState().chatFailures.first).toMatchObject({message:'Please plan my day',problem});
    expect(useAppStore.getState().chatFailures.second).toBeUndefined();
    clearWorkspace();
    expect(useAppStore.getState().chatFailures).toEqual({});
  });
  it('does not resurrect a deleted chat when its failed request returns',async()=>{
    let reject!:(error:Error)=>void;
    vi.mocked(api.coordinatorTurn).mockImplementation(()=>new Promise((_resolve,fail)=>{reject=fail;}));
    const pending=sendCoreMessage(first,'Hello');
    useAppStore.getState().removeConversation(first.id);
    reject(new Error('Connection lost'));await pending;
    expect(useAppStore.getState().chatFailures.first).toBeUndefined();
    expect(useAppStore.getState().chatInputs.first).toBeUndefined();
  });
  it("shows the reply while Mavo writes it, then the saved one",async()=>{
    const streams=api as unknown as {coordinatorTurnStream?:(onReply:(text:string)=>void,...rest:unknown[])=>Promise<CoordinatorResponse>};
    let seen:string|undefined;
    streams.coordinatorTurnStream=vi.fn(async(onReply:(text:string)=>void)=>{
      onReply("Hel");onReply("Hello the");
      seen=useAppStore.getState().streamingReplies.first;
      return reply;
    });
    try{
      await sendCoreMessage(first,"Hi",undefined,[],[],["file-1"]);
      expect(seen).toBe("Hello the");
      expect(streams.coordinatorTurnStream).toHaveBeenCalledWith(expect.any(Function),"Hi","first-session",undefined,expect.any(String),"first",[],[],["file-1"]);
      expect(useAppStore.getState().streamingReplies).toEqual({});
      expect(api.coordinatorTurn).not.toHaveBeenCalled();
    }finally{delete streams.coordinatorTurnStream;}
  });
});
