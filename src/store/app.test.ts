import { beforeEach, describe, expect, it } from "vitest";
import { clearWorkspace, useAppStore } from "./app";

describe("app store", () => {
  beforeEach(() => useAppStore.setState({ page: "home", conversations: [], crew: [], setupDrafts: [], deletedCrewIds: [], activeConversationId: null }));

  it("navigates between product areas", () => {
    useAppStore.getState().setPage("resources");
    expect(useAppStore.getState().page).toBe("resources");
  });

  it("lands on the graph from the old page names, with the chat open for chat", () => {
    useAppStore.setState({ page: "settings", chatOpen: false });
    useAppStore.getState().setPage("crew");
    expect(useAppStore.getState()).toMatchObject({ page: "home", chatOpen: false });
    useAppStore.getState().setPage("chat");
    expect(useAppStore.getState()).toMatchObject({ page: "home", chatOpen: true });
  });

  it("asks the graph to show an agent, and the chat to start one", () => {
    useAppStore.setState({ page: "actions", chatOpen: false, activeConversationId: "c1" });
    useAppStore.getState().openAgent("crew-9");
    expect(useAppStore.getState()).toMatchObject({ page: "home", focusAgent: "crew-9" });
    const focus = useAppStore.getState().chatFocus;
    useAppStore.getState().startInChat("Make an agent that ");
    expect(useAppStore.getState()).toMatchObject({ chatOpen: true, activeConversationId: null, chatFocus: focus + 1 });
    expect(useAppStore.getState().chatInputs.new).toBe("Make an agent that ");
  });

  it("clears private content and connection metadata when the account session ends", () => {
    useAppStore.setState({page: "settings", selectedRouteId: "private-route", activeConversationId: "private-chat"});
    useAppStore.getState().setRoutes([{id: "private-route", name: "Private connection", provider: "airouter", model: "test", has_credential: true, created_at: "now", updated_at: "now"}]);
    useAppStore.getState().setConversations([{id: "private-chat", title: "Private conversation", messages: [], created_at: "now", updated_at: "now"}]);
    clearWorkspace();
    expect(useAppStore.getState()).toMatchObject({page: "home", routes: [], conversations: [], crew: [], crewProposals: [], selectedRouteId: "", activeConversationId: null, status: null});
  });

  it("keeps the newest conversation first", () => {
    const conversation = { id: "one", title: "Test", created_at: "now", updated_at: "now", messages: [] };
    useAppStore.getState().upsertConversation(conversation);
    expect(useAppStore.getState().conversations[0]).toEqual(conversation);
    expect(useAppStore.getState().activeConversationId).toBe("one");
  });

  it("adds, updates, and removes persisted Crew state", () => {
    const crew = {
      id: "crew-one",
      template_id: "schedule",
      name: "College Scheduler",
      responsibilities: "Track college work.",
      autonomy_mode: "reactive" as const,
      status: "active" as const,
      provider: "airouter" as const,
      model: "airouter/free",
      route_id: "route-one",
      max_model_calls: 5,
      is_running: false,
      runtime_state: "stopped",
      review_interval_minutes: 30,
      daily_call_limit: 12,
      daily_calls_used: 0,
      next_run_at: null,
      last_run_at: null,
      last_runtime_error: null,
      checkpoint: null,
      trigger_type: "interval" as const,
      mail_sender: null,
      mail_subject: null,
      schedule_time: null,
      reminder_text: null,
      resources: [],
      permissions: ["model:invoke"],
      triggers: [],
      created_at: "now",
      updated_at: "now"
    };
    useAppStore.getState().upsertCrew({...crew,status:"draft"});
    expect(useAppStore.getState().crew).toHaveLength(0);
    expect(useAppStore.getState().setupDrafts).toHaveLength(1);
    useAppStore.getState().upsertCrew(crew);
    expect(useAppStore.getState().setupDrafts).toHaveLength(0);
    useAppStore.getState().upsertCrew({ ...crew, status: "paused" });
    expect(useAppStore.getState().crew).toHaveLength(1);
    expect(useAppStore.getState().crew[0].status).toBe("paused");
    useAppStore.getState().removeCrew(crew.id);
    expect(useAppStore.getState().crew).toEqual([]);
    useAppStore.getState().setCrew([crew]);
    useAppStore.getState().upsertCrew(crew);
    useAppStore.getState().setSetupDrafts([{...crew,status:"draft"}]);
    expect(useAppStore.getState().crew).toEqual([]);
    expect(useAppStore.getState().setupDrafts).toEqual([]);
  });

  it("preserves chat selection on refresh and clears deleted-chat draft state",()=>{
    const first={id:"one",title:"First",messages:[],created_at:"now",updated_at:"now"};
    const second={...first,id:"two",title:"Second"};
    const store=useAppStore.getState();
    store.setConversations([first,second]);store.setActiveConversation("two");
    store.setConversations([second,first]);
    expect(useAppStore.getState().activeConversationId).toBe("two");
    useAppStore.setState({chatInputs:{two:"Unsent"},chatErrors:{two:"Error"},chatRefinements:{two:"Crew"}});
    store.removeConversation("two");
    expect(useAppStore.getState()).toMatchObject({activeConversationId:"one",chatInputs:{},chatErrors:{},chatRefinements:{}});
    store.removeConversation("one");
    expect(useAppStore.getState().activeConversationId).toBeNull();
  });
});
