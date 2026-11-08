import type { CrewInstance, ModelRoute, ProviderStatus } from "../types";

export function usesModel(crew: CrewInstance): boolean {
  return !(crew.template_id === "schedule" && crew.trigger_type === "daily")
    && !(crew.template_id === "computer" && ["manual", "interval"].includes(crew.trigger_type));
}

/** Discovery is not an inference test, and cloud routes have independent API keys. */
export function routeAvailability(route: ModelRoute, providers?: ProviderStatus[]): boolean | undefined {
  if (route.provider !== "ollama") return route.has_credential === false ? false : undefined;
  const provider = providers?.find(p => p.id === "ollama");
  if (!provider) return undefined;
  return provider.connected && provider.models.some(m => m.name === route.model);
}

export function fallbackSummary(fallback: CrewInstance["fallback"]): string | null {
  if (!fallback) return null;
  const step = fallback.status === "completed" ? "Completed with"
    : fallback.status === "queued" ? "Retry queued on"
    : fallback.status === "failed" ? "Retry failed on"
    : fallback.status === "cancelled" ? "Retry cancelled for" : "Fallback recorded for";
  return `${step} ${fallback.to}. Original route: ${fallback.from}. ${fallback.why}`;
}
