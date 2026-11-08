import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, requestLimit, resetApiForTests } from "./api";

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
});
