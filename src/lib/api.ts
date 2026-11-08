import { invoke } from "@tauri-apps/api/core";
import { actionRuleInput } from "./actionRuleInput";
import type { CrewProposal, GoogleCapability, GoogleConnectionCheck, GoogleResource, GoogleStatus, WeatherLocation, ActionRequest, FirebaseStatus } from "../types";
import type { AppStatus, ChatResponse, Conversation, CrewInput, CrewInstance, CrewRun, CrewTemplate, ModelRoute, ModelRouteInput, ProviderConfiguration, ProviderId, Reminder, RouteTest, WeatherSnapshot } from "../types";

interface BackendConfig {
  base_url: string;
  token: string;
}

let cachedConfig: BackendConfig | null = null;

async function backendConfig(): Promise<BackendConfig> {
  if (cachedConfig) return cachedConfig;
  try {
    cachedConfig = await invoke<BackendConfig>("backend_config");
    return cachedConfig;
  } catch {
    const baseUrl = import.meta.env.VITE_MAVO_API_URL as string | undefined;
    const token = import.meta.env.VITE_MAVO_API_TOKEN as string | undefined;
    if (baseUrl && token) {
      cachedConfig = { base_url: baseUrl, token };
      return cachedConfig;
    }
    throw new Error("The Mavo backend is not running. Launch the app through Tauri.");
  }
}

/** How long each kind of request may take. A sample run waits on the agent's model, a sign-in check
 * is bounded by the backend, everything else is quick. */
export function requestLimit(path: string): number {
  if (path.startsWith('/runtime/activity')) return 4000;
  if (path === "/core/turn") return 215_000;
  if (/^\/crew\/[^/]+\/trial$/.test(path)) return 300_000;
  if (path === "/browser/probe") return 75_000;
  return 135_000;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const config = await backendConfig();
  const options = {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${config.token}`,
      ...init?.headers
    }
  };
  let response: Response | undefined;
  let networkError: unknown;
  const attempts = path.startsWith('/runtime/activity')?1:!init?.method || init.method === "GET" ? 20 : 1;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      response = await fetch(`${config.base_url}/api/v1${path}`, { ...options, signal: AbortSignal.timeout(requestLimit(path)) });
      break;
    } catch (error) {
      networkError = error;
      await new Promise((resolve) => window.setTimeout(resolve, 150));
    }
  }
  if (!response) {
    // The browser's own wording for an expired timer is "signal timed out": say what happened.
    if (networkError instanceof DOMException && (networkError.name === "TimeoutError" || networkError.name === "AbortError")) throw new Error("This is taking longer than expected.");
    throw new Error(networkError instanceof Error ? networkError.message : "Mavo Core did not start.");
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string | { msg: string; loc: (string | number)[] }[] };
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((item) => `${item.loc.slice(1).join(".")}: ${item.msg}`).join("; ");
    } catch {
      // The status code remains a useful, non-sensitive fallback.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  runtimeActivity:(after?:number)=>request<import('./executionSignals').RuntimeSnapshot>(`/runtime/activity${after===undefined?'':`?after=${after}`}`),
  trialCrew:(id:string)=>request<import('../components/SampleRun').TrialResult>(`/crew/${id}/trial`,{method:"POST"}),
  startTrial:(id:string)=>request<import('../components/SampleRun').TrialStatus>(`/crew/${id}/trial/start`,{method:"POST"}),
  trialStatus:(id:string)=>request<import('../components/SampleRun').TrialStatus>(`/crew/${id}/trial`),
  logins:()=>request<import('../components/SaveLogin').SavedLogin[]>("/browser/logins"),
  saveLogin:(site:string,username:string,password:string)=>request<import('../components/SaveLogin').SavedLogin>("/browser/logins",{method:"PUT",body:JSON.stringify({site,username,password})}),
  deleteLogin:(host:string)=>request<void>(`/browser/logins/${encodeURIComponent(host)}`,{method:"DELETE"}),
  probeSite:(url:string)=>request<{host:string;url:string|null;signed_in:boolean|null;session?:"in"|"likely"|"out"|"unknown";unknown?:boolean;saved_session?:boolean|null;wall:{kind:"login"|"captcha";host:string}|null;login_url?:string|null;seconds:number}>("/browser/probe",{method:"POST",body:JSON.stringify({url})}),
  autostart:()=>request<{supported:boolean;enabled:boolean;mode:"app"|"development"|null}>("/system/autostart"),
  setAutostart:(enabled:boolean)=>request<{supported:boolean;enabled:boolean;mode:"app"|"development"|null}>("/system/autostart",{method:"PUT",body:JSON.stringify({enabled})}),
  siteSaved:(host:string)=>request<{host:string;saved:boolean|null}>(`/browser/saved?host=${encodeURIComponent(host)}`),
  browserState:()=>request<{engine:string;open:boolean;busy:boolean;seconds_left:number}>("/browser"),
  browserSignIn:(url:string)=>request<{opened:boolean;engine:string}>("/browser/sign-in",{method:"POST",body:JSON.stringify({url})}),
  browserClose:()=>request<{open:boolean}>("/browser/close",{method:"POST"}),
  pool:()=>request<import('../types').ResourcePool>("/pool"),
  setPoolLevel:(id:string,level:import('../types').AccessLevel)=>request<import('../types').PoolResource>(`/pool/${encodeURIComponent(id)}`,{method:"PUT",body:JSON.stringify({level})}),
  timeline:(days=7)=>request<import('../types').Timeline>(`/timeline?days=${days}`),
  crewGraph:()=>request<import('../types').CrewGraphData>("/crew-graph"),
  githubAuth:()=>request<import('../types').GitHubAuthStatus>("/connectors/github/auth"),
  startGitHub:(simple=true)=>request<import('../types').GitHubAuthStatus>("/connectors/github/start",{method:"POST",body:JSON.stringify({simple})}),
  revokeGitHub:()=>request<import('../types').RevocationReceipt>("/connectors/github/revoke",{method:"POST"}),
  confirmGitHubRevoked:()=>request<import('../types').RevocationReceipt>("/connectors/github/revocation/confirm",{method:"POST"}),
  revokeGoogle:()=>request<import('../types').RevocationReceipt>("/auth/google/revoke",{method:"POST"}),
  cancelGitHub:()=>request<import('../types').GitHubAuthStatus>("/connectors/github/cancel",{method:"POST"}),
  reviewGitHub:()=>request<import('../types').GitHubAuthStatus>("/connectors/github/review",{method:"POST"}),
  reloadGitHubRepositories:()=>request<import('../types').GitHubAuthStatus>("/connectors/github/repositories",{method:"POST"}),
  authorizeGitHub:(repositories:string[],capabilities:string[])=>request<import('../types').ConnectorStatus>("/connectors/github/authorize",{method:"POST",body:JSON.stringify({repositories,connector_capabilities:capabilities})}),
  connectors:()=>request<import('../types').ConnectorStatus[]>("/connectors"),
  connectGitHub:(token:string,repositories:string[],capabilities:string[])=>request("/connectors/github/connect",{method:"POST",body:JSON.stringify({token,repositories,connector_capabilities:capabilities})}),
  connectFeeds:(sources:string[])=>request<import('../types').ConnectorStatus>("/connectors/feeds/connect",{method:"POST",body:JSON.stringify({sources})}),
  disconnectConnector:(provider:string)=>request<void>(`/connectors/${encodeURIComponent(provider)}`,{method:"DELETE"}),
  actionPolicies: (crewId?: string,includeUnavailable=false) => request<{ paused: boolean; actions: Array<{ operation: string; group: string; label: string; provider:string;available:boolean;requires_review: boolean; rule: import('../components/ActionAccessEditor').ActionRule; arguments_schema: Record<string, unknown> }> }>(`/action-policies?include_unavailable=${includeUnavailable}${crewId ? '&crew_id=' + encodeURIComponent(crewId) : ''}`),
  actionAssignments: (operation: string) => request<Array<{id:string;name:string;status:import('../types').CrewStatus;responsibilities:string;authorized:boolean;rule:import('../components/ActionAccessEditor').ActionRule}>>(`/action-policies/${encodeURIComponent(operation)}/crew`),
  openCrewBrowser: (id:string,url:string) => request<{opened:boolean;paused:boolean;message:string}>(`/crew/${id}/browser-session`,{method:"POST",body:JSON.stringify({url})}),
  crewBrowserStatus: (id:string) => request<{open:boolean;signing_in:boolean}>(`/crew/${id}/browser-session`),
  saveActionPolicy: (operation: string, rule: import('../components/ActionAccessEditor').ActionRule, crewId?: string) => request('/action-policies', {method: 'PUT', body: JSON.stringify({operation, rule: actionRuleInput(rule), crew_id: crewId ?? null})}),
  setAllActions: (mode:"off"|"limited"|"unrestricted", crewId?:string, confirm=false) => request<import('../types').BulkResult>('/action-policies/bulk', {method: 'PUT', body: JSON.stringify({mode, crew_id: crewId ?? null, confirm})}),
  runApprovals: () => request<import('../types').RunApproval[]>('/projects/approvals'),
  approveRun: (id:string, sha256:string, trust:boolean, automation?:import('../types').AutomationChoice) => request<{status:string}>(`/actions/${encodeURIComponent(id)}/approve`, {method: 'POST', body: JSON.stringify({sha256, trust, ...(automation?{automation}:{})})}),
  automations: () => request<import('../types').AutomationInfo[]>('/automations'),
  runAutomation: (name:string) => request<{status:string;output?:string;problem?:string}>(`/automations/${encodeURIComponent(name)}/run`, {method: 'POST'}),
  removeAutomation: (name:string) => request<void>(`/automations/${encodeURIComponent(name)}`, {method: 'DELETE'}),
  rejectAction: (id:string) => request<{status:string}>(`/actions/${encodeURIComponent(id)}/reject`, {method: 'POST'}),
  projects: () => request<{folder:string|null;error?:string;projects:import('../types').ProjectInfo[]}>('/projects'),
  trustProject: (name:string, trusted:boolean) => request<{project:string;trusted:boolean}>(`/projects/${encodeURIComponent(name)}/trust`, {method: 'PUT', body: JSON.stringify({trusted})}),
  tasks: (conversationId?:string) => request<import('../types').TaskRun[]>('/tasks'+(conversationId?`?conversation_id=${encodeURIComponent(conversationId)}`:'')),
  task: (id:string) => request<import('../types').TaskRun>(`/tasks/${encodeURIComponent(id)}`),
  stopTask: (id:string) => request<import('../types').TaskRun>(`/tasks/${encodeURIComponent(id)}/stop`, {method: 'POST'}),
  retryTask: (id:string) => request<import('../types').TaskRun>(`/tasks/${encodeURIComponent(id)}/retry`, {method: 'POST'}),
  answerTask: (id:string, answer:string, step_id?:string) => request<import('../types').TaskRun>(`/tasks/${encodeURIComponent(id)}/answer`, {method: 'POST', body: JSON.stringify({answer, step_id})}),
  pauseActions: () => request('/actions/pause', {method: 'POST'}),
  resumeActions: () => request('/actions/resume', {method: 'POST'}),
  desktopStatus: () => request<{paused: boolean; running: number; pending: number; needs?: {id: string; name: string; reason: string}[]; working?: string[]}>('/desktop-status'),

  status: () => request<AppStatus>("/status"),
  coreConversation: (id?:string) => request<{ conversation: Conversation | null; crew_proposals: CrewProposal[]; planning_crew_id?:string | null; setup_drafts?:CrewInstance[] }>(`/core/conversation${id?'?conversation_id='+encodeURIComponent(id):''}`),
  coreConversations: () => request<Conversation[]>("/core/conversations"),
  createCoreConversation: () => request<Conversation>("/core/conversations",{method:"POST"}),
  renameConversation: (id:string,title:string) => request<Conversation>(`/conversations/${encodeURIComponent(id)}`,{method:"PATCH",body:JSON.stringify({title})}),
  crewDrafts: () => request<CrewInstance[]>("/crew-drafts"),
  coreStatus: () => request<import('../types').CoreStatus>("/core/status"),
  coordinatorTurn: (message: string, sessionId = "main", crewId?: string, requestId = crypto.randomUUID(), conversationId?:string) => request<import('../types').CoordinatorResponse>("/core/turn", {method:"POST",body:JSON.stringify({message,session_id:sessionId,crew_id:crewId??null,request_id:requestId,conversation_id:conversationId??null})}),
  setCrewAccess: (id:string, payload:{capabilities:GoogleCapability[];connector_capabilities?:string[];actions:Record<string,import('../components/ActionAccessEditor').ActionRule>;notifications:boolean;device_metrics:boolean;temp_cleanup?:boolean;weather?:boolean;route_id:string|null}) => request<CrewInstance>(`/crew/${id}/access`,{method:"POST",body:JSON.stringify({...payload,actions:Object.fromEntries(Object.entries(payload.actions).map(([operation,rule])=>[operation,actionRuleInput(rule)]))})}),
  openProject: (name:string, what:"app"|"folder") => request<void>(`/projects/${encodeURIComponent(name)}/open`,{method:"POST",body:JSON.stringify({what})}),
  mavoAgents: () => request<import('../types').MavoAgent[]>("/mavo-agents"),
  crewAsks: (id:string) => request<import('../types').CrewAsk[]>(`/crew/${encodeURIComponent(id)}/asks`),
  askCrew: (id:string,question:string) => request<import('../types').CrewAsk>(`/crew/${encodeURIComponent(id)}/asks`,{method:"POST",body:JSON.stringify({question})}),
  forgetPreference: (id:string,index:number) => request<CrewInstance>(`/crew/${encodeURIComponent(id)}/preferences/${index}`,{method:"DELETE"}),
  memory: () => request<import('../types').MemoryFact[]>("/memory"),
  addMemory: (text:string) => request<import('../types').MemoryFact[]>("/memory",{method:"POST",body:JSON.stringify({text})}),
  forgetMemory: (id:string) => request<import('../types').MemoryFact[]>(`/memory/${encodeURIComponent(id)}`,{method:"DELETE"}),
  clearAsks: (id:string) => request<void>(`/crew/${encodeURIComponent(id)}/asks`,{method:"DELETE"}),
  replyCrew: (id:string,message:string) => request<CrewInstance>(`/crew/${id}/reply`,{method:"POST",body:JSON.stringify({message})}),
  dismissProposal: (id: string) => request<void>(`/core/proposals/${id}/dismiss`, { method: "POST" }),
  googleStatus: () => request<GoogleStatus>("/auth/google"),
  configureGoogle: (clientId: string, secret?: string) => request<GoogleStatus>("/auth/google/client", { method: "PUT", body: JSON.stringify({ client_id: clientId, ...(secret ? { client_secret: secret } : {}) }) }),
  signInGoogle: (resources: GoogleResource[]) => request<{ authorization_url: string }>("/auth/google/start", { method: "POST", body: JSON.stringify({ resources }) }),
  connectAllGoogle: () => request<{ authorization_url: string }>("/auth/google/start", { method: "POST", body: JSON.stringify({ all_services: true }) }),
  connectGoogleCapabilities: (capabilities: GoogleCapability[]) => request<{ authorization_url: string }>("/auth/google/start", { method: "POST", body: JSON.stringify({ capabilities }) }),
  messageBody: (id: string) => request<{ id: string; body: string }>(`/resources/google/gmail/messages/${encodeURIComponent(id)}`),
  googleItem: (service: GoogleResource, id: string) => request<Record<string,unknown>>(`/resources/google/${service}/items/${encodeURIComponent(id)}`),
  actions: () => request<ActionRequest[]>("/actions"),
  proposeAction: (operation: string, args: Record<string, unknown>) => request<ActionRequest>("/actions", { method: "POST", body: JSON.stringify({ operation, arguments: args }) }),
  firebaseStatus: () => request<FirebaseStatus>("/auth/firebase"),
  firebaseStart: () => request<{ authorization_url: string }>("/auth/firebase/start", { method: "POST" }),
  firebaseCancel: () => request<FirebaseStatus>("/auth/firebase/cancel", { method: "POST" }),
  firebaseSignOut: () => request<void>("/auth/firebase", { method: "DELETE" }),
  cancelGoogleSignIn: () => request<GoogleStatus>("/auth/google/cancel", { method: "POST" }),
  checkGoogleConnection: () => request<GoogleConnectionCheck>("/auth/google/check", { method: "POST" }),
  disconnectGoogle: () => request<void>("/auth/google", { method: "DELETE" }),
  googleResource: (resource: GoogleResource) => request<{ resource: string; coverage: string; fetched_at: string; items: Record<string, unknown>[] }>(`/resources/google/${resource}`),
  weatherLocations: (query: string, country?: string) => request<WeatherLocation[]>(`/resources/weather/locations?query=${encodeURIComponent(query)}${country ? `&country_code=${country}` : ""}`),
  resourceAccess: () => request<{weather: boolean; location: boolean}>("/resources/access"),
  deviceLocation: () => request<WeatherLocation | null>("/resources/location"),
  saveDeviceLocation: (location: WeatherLocation) => request<WeatherLocation>("/resources/location", {method: "PUT", body: JSON.stringify(location)}),
  setResourceAccess: (access: {weather: boolean; location: boolean}) => request<{weather: boolean; location: boolean}>("/resources/access", {method: "PUT", body: JSON.stringify(access)}),
  savedWeatherLocation: () => request<WeatherLocation | null>("/resources/weather/location"),
  weatherForecast: (location: WeatherLocation) => request<WeatherSnapshot>("/resources/weather/forecast", { method: "POST", body: JSON.stringify(location) }),
  conversations: () => request<Conversation[]>("/conversations"),
  deleteConversation: (id: string) => request<void>(`/conversations/${id}`, { method: "DELETE" }),
  crewTemplates: () => request<CrewTemplate[]>("/crew/templates"),
  crew: () => request<CrewInstance[]>("/crew"),
  routes: () => request<ModelRoute[]>("/routes"),
  testRoute: (id: string) => request<RouteTest>(`/routes/${id}/test`, { method: "POST" }),
  crewRuns: (id?: string) => request<CrewRun[]>(`/crew-runs${id ? `?crew_id=${encodeURIComponent(id)}` : ""}`),
  runCrew: (id: string, message: string) => request<CrewRun>(`/crew/${id}/run`, { method: "POST", body: JSON.stringify({ message }) }),
  startCrew: (id: string) => request<CrewInstance>(`/crew/${id}/start`, { method: "POST" }),
  stopCrew: (id: string) => request<CrewInstance>(`/crew/${id}/stop`, { method: "POST" }),
  /** Approve work held for you, or send it back to the agent that made it, with a note. */
  answerHandover: (id: string, approve: boolean, note = "") => request<CrewInstance>(`/crew/${id}/handover`, { method: "POST", body: JSON.stringify({ approve, note }) }),
  checkCrew: (id: string) => request<CrewInstance>(`/crew/${id}/check`, { method: "POST" }),
  chat: (message: string, routeId: string, conversationId?: string) =>
    request<ChatResponse>("/chat", {
      method: "POST",
      body: JSON.stringify({ message, route_id: routeId, conversation_id: conversationId ?? null })
    }),
  createRoute: (payload: ModelRouteInput) => request<ModelRoute>("/routes", { method: "POST", body: JSON.stringify(payload) }),
  updateRoute: (id: string, payload: Partial<ModelRouteInput>) => request<ModelRoute>(`/routes/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteRoute: (id: string) => request<void>(`/routes/${id}`, { method: "DELETE" }),
  configureProvider: (provider: ProviderId, apiKey: string, defaultModel: string) =>
    request<ProviderConfiguration>(`/providers/${provider}`, {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey, default_model: defaultModel })
    }),
  disconnectProvider: (provider: ProviderId) =>
    request<void>(`/providers/${provider}`, { method: "DELETE" }),
  createCrew: (payload: CrewInput) =>
    request<CrewInstance>("/crew", { method: "POST", body: JSON.stringify(payload) }),
  updateCrew: (id: string, payload: Partial<Omit<CrewInput, "template_id">> & { fallback_route_id?: string | null; status?: "active" | "paused"; upstream?: string[]; review?: boolean; approve_first?: boolean; max_rounds?: number }) =>
    request<CrewInstance>(`/crew/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteCrew: (id: string) => request<void>(`/crew/${id}`, { method: "DELETE" }),
  weather: (location: string) => request<WeatherSnapshot>(`/resources/weather?location=${encodeURIComponent(location)}`),
  dueReminders: () => request<Reminder[]>("/reminders/due"),
  reminderHistory: () => request<Reminder[]>("/reminders"),
  dismissReminder: (id: string) => request<void>(`/reminders/${id}/dismiss`, { method: "POST" })
};

export function resetApiForTests(): void {
  cachedConfig = null;
}
