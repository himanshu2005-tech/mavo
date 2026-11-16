import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, requestLimit, resetApiForTests } from "./api";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => ({ base_url: "http://127.0.0.1:8321", token: "test-only-token" })) }));

describe("authenticated API boundary", () => {
  beforeEach(() => { resetApiForTests(); vi.restoreAllMocks(); vi.stubGlobal("window", { setTimeout: (callback: () => void) => { callback(); return 0; } }); });
  it("sends bearer authentication and uses only the selected route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true })));
    vi.stubGlobal("fetch", fetchMock);
    await api.chat("Hello", "my-route");
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8321/api/v1/chat");
    expect(options.headers.Authorization).toBe("Bearer test-only-token");
    expect(JSON.parse(options.body).route_id).toBe("my-route");
  });
  it("presents schema errors as readable text", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [{ loc: ["body", "schedule_time"], msg: "Invalid schedule time" }] }), { status: 422 })));
    await expect(api.updateCrew("one", { schedule_time: "99:00" })).rejects.toThrow("schedule_time: Invalid schedule time");
  });
  it("gives slow work the time it needs and never shows the browser's raw timer message", async () => {
    expect(requestLimit("/crew/abc/trial")).toBeGreaterThan(requestLimit("/browser/probe"));
    expect(requestLimit("/browser/probe")).toBeLessThan(requestLimit("/crew"));
    const fetchMock = vi.fn().mockRejectedValue(new DOMException("signal timed out", "TimeoutError"));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.probeSite("https://portal.example.com")).rejects.toThrow("This is taking longer than expected.");
  });
  it("does not automatically replay a model call after a network failure", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("Connection lost"));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.runCrew("one", "Think about this")).rejects.toThrow("Connection lost");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("requests all supported Google services through one authenticated consent call", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ authorization_url: "https://accounts.google.com/o/oauth2/v2/auth" })));
    vi.stubGlobal("fetch", fetchMock);
    await api.connectAllGoogle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8321/api/v1/auth/google/start");
    expect(options.method).toBe("POST");
    expect(options.headers.Authorization).toBe("Bearer test-only-token");
    expect(JSON.parse(options.body)).toEqual({ all_services: true });
  });
  it("cancels a pending consent without disconnecting the existing Google account", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ phase: "idle" })));
    vi.stubGlobal("fetch", fetchMock);
    await api.cancelGoogleSignIn();
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8321/api/v1/auth/google/cancel");
    expect(options.method).toBe("POST");
  });
  it('preserves structured Core failures and never automatically repeats inference',async()=>{
    const problem={code:'core_provider_credits',message:'Insufficient Core credits.',action:'Publisher top-up required.',retryable:false,request_id:'reference'};
    const fetchMock=vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:problem.message,error:problem}),{status:503}));
    vi.stubGlobal('fetch',fetchMock);
    try{await api.coordinatorTurn('Hello');expect.unreachable();}catch(error){expect(error).toBeInstanceOf(ApiError);expect((error as ApiError).problem).toEqual(problem);}
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('does not claim nothing changed when a chat response is lost',async()=>{
    const fetchMock=vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch',fetchMock);
    await expect(api.coordinatorTurn('Do a task')).rejects.toMatchObject({problem:{code:'core_delivery_unknown',retryable:false}});
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('bounds Core status checks and does not repeat an expired GET timeout',async()=>{
    expect(requestLimit('/core/status')).toBe(10_000);
    const fetchMock=vi.fn().mockRejectedValue(new DOMException('timeout','TimeoutError'));
    vi.stubGlobal('fetch',fetchMock);
    await expect(api.coreStatus()).rejects.toThrow('taking longer than expected');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('bounds desktop polling failures so the next check can retry promptly',async()=>{
    expect(requestLimit('/reminders/due')).toBe(4000);
    expect(requestLimit('/auth/firebase')).toBe(4000);
    const fetchMock=vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch',fetchMock);
    await expect(api.dueReminders()).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('classifies account startup network failures without echoing browser fetch errors',async()=>{
    const fetchMock=vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch',fetchMock);
    await expect(api.firebaseStatus()).rejects.toMatchObject({status:0,message:'Mavo’s local service is not reachable yet.'});
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('forces a fresh Core health check without invoking a model',async()=>{
    expect(requestLimit('/core/status?refresh=true')).toBe(10_000);
    const fetchMock=vi.fn().mockResolvedValue(new Response(JSON.stringify({configured:true,connected:true})));
    vi.stubGlobal('fetch',fetchMock);
    await api.coreStatus(true);
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8321/api/v1/core/status?refresh=true');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
