import { expect, it } from "vitest";
import { fallbackSummary, routeAvailability, usesModel } from "./modelMap";
import type { CrewInstance, ModelRoute, ProviderStatus } from "../types";

const local = { id: "local", provider: "ollama", model: "small" } as ModelRoute;
const provider = { id: "ollama", connected: true, models: [{ name: "small" }] } as ProviderStatus;

it("does not invent model work for deterministic reminders or device monitors", () => {
  const crew = { template_id: "schedule", trigger_type: "daily" } as CrewInstance;
  expect(usesModel(crew)).toBe(false);
  expect(usesModel({ ...crew, template_id: "communication" })).toBe(true);
  expect(usesModel({ ...crew, template_id: "computer", trigger_type: "interval" })).toBe(false);
});

it("does not claim a cloud route's independent key works based on provider discovery", () => {
  expect(routeAvailability({ ...local, provider: "airouter" }, [provider])).toBeUndefined();
  expect(routeAvailability(local)).toBeUndefined();
  expect(routeAvailability({ ...local, provider: "airouter", has_credential: false })).toBe(false);
});

it("requires Ollama to be available and the chosen model to be installed", () => {
  expect(routeAvailability(local, [provider])).toBe(true);
  expect(routeAvailability({ ...local, model: "missing" }, [provider])).toBe(false);
  expect(routeAvailability(local, [{ ...provider, connected: false }])).toBe(false);
});

it("never presents a queued or failed fallback as completed work", () => {
  const entry = { from: "Local", to: "Cloud", why: "Invalid checkpoint", at: "" };
  expect(fallbackSummary(null)).toBeNull();
  expect(fallbackSummary(entry)).toContain("Fallback recorded");
  expect(fallbackSummary({ ...entry, status: "queued" })).toContain("Retry queued");
  expect(fallbackSummary({ ...entry, status: "failed" })).toContain("Retry failed");
  expect(fallbackSummary({ ...entry, status: "completed" })).toContain("Completed with Cloud");
});
