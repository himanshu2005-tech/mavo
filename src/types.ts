export type NavId = "home" | "resources" | "actions" | "settings" | "timeline" | "activity";

export interface ModelInfo {
  name: string;
  size: number | null;
  modified_at: string | null;
}

export interface AppStatus {
  service: "mavo";
  version: string;
  database: "connected";
  providers: ProviderStatus[];
}

export type ProviderId = "ollama" | "openai" | "anthropic" | "openrouter" | "airouter";

export interface ProviderStatus {
    id: ProviderId;
    name: string;
    local: boolean;
    configured: boolean;
    connected: boolean;
    url: string;
    models: ModelInfo[];
    default_model: string | null;
    error: string | null;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  model: string | null;
  provider: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: Message[];
  session_id?: string;
  planning_crew_id?: string | null;
  planning_crew_ids?: string[];
}

export interface ChatResponse {
  conversation: Conversation;
  assistant_message: Message;
  crew_proposals: CrewProposal[];
  action_requests?: ActionRequest[];
}

export interface ProviderConfiguration {
  provider: ProviderId;
  default_model: string;
  has_credential: boolean;
}

export interface ModelRoute {
  id: string;
  name: string;
  provider: ProviderId;
  model: string;
  has_credential: boolean;
  created_at: string;
  updated_at: string;
}

export interface ModelRouteInput {
  name: string;
  provider: ProviderId;
  model: string;
  api_key?: string;
}

export type AutonomyMode = "reactive" | "proactive" | "continuous";
export type CrewStatus = "active" | "paused" | "draft";

export interface CrewTemplate {
  id: string;
  name: string;
  purpose: string;
  supported_capabilities: string[];
  compatible_resources: string[];
  default_permissions: string[];
  suggested_triggers: string[];
  implementation_status: "manual_assistance" | "daily_reminders";
  behavior: string;
  default_interval_minutes: number;
}

export interface DailyTrigger { id?: string; time: string; message: string; weekdays: number[]; enabled: boolean }
export interface CrewInstance {
  daily_triggers?: DailyTrigger[];
  question?: string | null;
  /** Work handed over that waits for your OK (approve it, or send it back). */
  held?: HeldWork | null;
  /** An AI-call limit that stopped its last run, and a limit that would let it finish. */
  limit?: CallLimit | null;
  /** A once-a-day run skipped because Mavo was not running at its time. */
  skipped_run?: { day: string; why: string } | null;
  /** The last run its local model could not do, done instead on a cloud model. */
  fallback?: { from: string; to: string; why: string; at: string; status?: "queued" | "completed" | "failed" | "cancelled" } | null;
  fallback_route_id?: string | null;
  id: string;
  template_id: string;
  name: string;
  responsibilities: string;
  configuration_plan?: CrewPlan;
  autonomy_mode: AutonomyMode;
  status: CrewStatus;
  provider: ProviderId | "unassigned";
  model: string;
  route_id: string | null;
  max_model_calls: number;
  schedule_time: string | null;
  reminder_text: string | null;
  resources: string[];
  permissions: string[];
  triggers: string[];
  created_at: string;
  updated_at: string;
  is_running: boolean;
  runtime_state: string;
  review_interval_minutes: number;
  daily_call_limit: number;
  daily_calls_used: number;
  next_run_at: string | null;
  last_run_at: string | null;
  last_runtime_error: string | null;
  checkpoint: string | null;
  trigger_type: TriggerType;
  mail_sender: string | null;
  mail_subject: string | null;
}

export interface DeviceMonitor {kind:"system"|"storage";drive:string;notifications:"alerts"|"each_check"|"changes";min_free_gb:number;battery_drain_per_hour?:number|null;clean_temp_days?:number|null;empty_recycle_bin?:boolean}
export interface CrewInput {
  connector_capabilities?: string[];
  template_id: string;
  name: string;
  responsibilities: string;
  autonomy_mode: AutonomyMode;
  route_id: string;
  max_model_calls: number;
  schedule_time?: string | null;
  reminder_text?: string | null;
  review_interval_minutes?: number;
  daily_call_limit?: number;
  allow_device_metrics?: boolean;
  allow_temp_cleanup?: boolean;
  allow_notifications?: boolean;
  device_monitor?: DeviceMonitor | null;
  allow_weather?: boolean;
  trigger_type?: TriggerType;
  google_resources?: GoogleResource[];
  google_capabilities?: GoogleCapability[];
  mail_sender?: string | null;
  mail_subject?: string | null;
}

export type TriggerType = "manual" | "interval" | "daily" | "gmail_message" | "calendar_changed" | "tasks_changed";
export type GoogleResource = "gmail" | "calendar" | "tasks" | "drive" | "contacts" | "youtube";
export type GoogleCapability = "gmail:read" | "gmail:body" | "gmail:send" | "gmail:modify" | "calendar:read" | "calendar:write" | "tasks:read" | "tasks:write" | "drive:read" | "contacts:read" | "youtube:read";
export interface GoogleStatus {
  revocation?:RevocationReceipt|null;
  configured: boolean;
  publisher_managed: boolean;
  developer_setup_allowed: boolean;
  client_id: string;
  has_client_secret: boolean;
  signed_in: boolean;
  name: string | null;
  email: string | null;
  resources: GoogleResource[];
  phase: "idle" | "pending" | "exchanging" | "complete" | "error";
  error: string | null;
  error_code?: string | null;
  missing_resources?: GoogleResource[];
  capabilities?: GoogleCapability[];
  missing_capabilities?: GoogleCapability[];
}
export interface GoogleConnectionCheck {
  checked_at: string;
  services: { resource: GoogleResource; status: "connected" | "not_authorized" | "error"; detail: string }[];
}
export interface WeatherLocation {
  source?: "manual" | "device";
  accuracy_m?: number | null;
  id?: number | null;
  name: string;
  region?: string;
  country?: string;
  country_code?: string;
  latitude: number;
  longitude: number;
  timezone?: string | null;
}
export interface CrewProposal {
  id: string;
  status: "proposed" | "accepted" | "dismissed";
  config: CrewInput & { requested_google_resources: GoogleResource[]; requested_google_capabilities?: GoogleCapability[] };
  created_crew_id: string | null;
}

/** One of Mavo's own agents, called in the chat as "@id ...". */
export interface MavoAgent { id: string; name: string; tagline: string; example: string }

/** One question to a Crew and its answer. `pending` while the Crew is finding it out. */
export interface CrewAsk { id: string; question: string; answer: string | null; pending: boolean; at: string; task_id?: string | null; kind?: "question" | "preference" | "fact" }
/** Something the person told Mavo about themselves, known by every agent. */
export interface MemoryFact { id: string; text: string; source: string; source_id?: string | null; at: string }

export interface Reminder {
  kind?: string;
  id: string;
  crew_id: string;
  crew_name: string;
  message: string;
  scheduled_for: string;
  local_date: string;
  status?: string;
  preview?: boolean;
  /** A one-time task's alert: its run, and the step whose question it shows. */
  task_run_id?: string | null;
  task_step_id?: string | null;
}

/** A script run waiting for the person: the scripts they would be approving, and whether they changed. */
export interface RunApproval {
  id: string; crew_id: string | null; crew_name: string | null; created_at: string;
  sha256: string | null; changed: boolean; trusted: boolean; project: string;
  tool?: "python" | "blender"; tool_path?: string; script?: string | null; file?: string | null;
  show?: boolean; args?: string[]; error?: string;
  sources?: { path: string; text: string; truncated: boolean }[];
}
export interface BulkResult {
  changed: { operation: string; label: string }[];
  skipped: { operation: string; label: string; reason: string }[];
}
/** A saved script run: approved once, tied to those exact scripts, run again with no model. */
export interface AutomationInfo { name: string; slug: string; project: string | null; tool: string | null; script: string | null; file: string | null; show: boolean; args: string[]; every_minutes: number | null; notify: "never" | "failure" | "always"; approved_at: string | null; last_run_at: string | null; last_status: "ok" | "failed" | "changed" | "blocked" | null; last_output: string }
export interface AutomationChoice { name: string; every_minutes: number | null; notify: "never" | "failure" | "always" }
export interface ProjectInfo { project: string; folder: string; files: number; trusted: boolean }
export interface ActionRequest {
  id: string; crew_id: string | null; crew_name: string | null; source: string;
  operation: string; arguments: Record<string, unknown>; status: string;
  target_summary?: string | null;
  approved_by?: string | null;
  result: Record<string, unknown> | null; error: string | null;
  created_at: string; finished_at: string | null;
}
export interface FirebaseStatus {
  configured: boolean; signed_in: boolean; email: string | null; name: string | null;
  phase: string; error: string | null;
}

export interface CrewRun {
  id: string;
  crew_id: string;
  crew_name: string;
  route_name: string;
  provider: string;
  model: string;
  prompt: string;
  status: "running" | "completed" | "failed" | "interrupted" | "cancelled";
  result: string | null;
  error: string | null;
  model_calls: number;
  created_at: string;
  finished_at: string | null;
  kind: string;
}

export interface RouteTest {
  route_id: string;
  model: string;
  response: string;
  latency_ms: number;
}

export interface WeatherSnapshot {
  provider: "open-meteo";
  location: string;
  latitude: number;
  longitude: number;
  timezone: string;
  fetched_at: string;
  cached: boolean;
  current: {
    temperature_c: number;
    apparent_temperature_c: number;
    precipitation_mm: number;
    wind_speed_kmh: number;
    weather_code: number;
    is_day: boolean;
  };
  forecast: Array<{
    date: string;
    weather_code: number;
    temperature_max_c: number;
    temperature_min_c: number;
    precipitation_probability_max: number;
    sunrise: string;
    sunset: string;
  }>;
}

export interface SuggestedAction { operation: string; reason: string; folders: string[]; domains: string[]; apps: string[]; recipients: string[]; repositories?:string[] }
export interface CrewPlan { upstream?: string[]; review?: boolean; approve_first?: boolean; max_rounds?: number; leads_team?: boolean; lead?: string | null; members?: string[]; run_until?: string | null; goal?: string | null; finished?: { at: string; why: string }; preferences?: { text: string; at: string }[]; group?: string | null; phase: string; questions: string[]; missing?: string[]; denied_resources?: GoogleCapability[]; device_monitor?:DeviceMonitor; draft: { name: string; responsibilities: string; template_id: string; trigger_type: TriggerType; schedule_time?: string | null; daily_triggers?: DailyTrigger[]; depends_on?: string[]; route_id?: string | null; requested_google_capabilities: GoogleCapability[]; requested_google_resources: GoogleResource[]; requested_connector_capabilities?:string[]; requested_actions: SuggestedAction[]; requested_notifications: boolean; requested_device_metrics: boolean; device_monitor?:DeviceMonitor|null; requested_weather?: boolean; daily_call_limit: number; max_model_calls: number; run_until?: string | null; goal?: string | null } }
export interface RevocationReceipt {provider:string;local_disconnected:boolean;remote_revoked:boolean;requires_provider_action:boolean;message:string;manage_url:string;at:string}
export interface ConnectorStatus {provider:"google"|"github"|"feeds";name:string;configured:boolean;connected:boolean;email:string|null;capabilities:string[];available_capabilities:string[];repositories?:string[];sources?:string[];available_sources?:{id:string;name:string;category?:string}[];error?:string|null;auth_mode?:"token"|"github_app"|null;revocation?:RevocationReceipt|null}
export interface GitHubAuthStatus {configured:boolean;phase:"idle"|"starting"|"pending"|"exchanging"|"installing"|"select_repositories"|"complete"|"error";user_code:string|null;verification_uri:string;installation_url:string|null;repositories:{full_name:string;capabilities:string[]}[];coverage:string;error:string|null;simple?:boolean}
/** One step of a one-time task, and how it is going. */
export interface TaskStepView { id: string; title: string; kind: "helper" | "agent" | "crew" | "automation" | "skill"; status: "waiting" | "running" | "done" | "failed" | "blocked" | "skipped" | "stopped"; output?: string; error?: string; question?: string; ask?: string | null; asked_at?: string; choices?: string[] | null; team?: string[]; thinking?: Thought[] }
export interface Thought { label: string; text: string }
export interface TaskRun { id: string; title: string; status: "running" | "done" | "needs_you" | "failed" | "stopped"; steps: TaskStepView[]; result: string | null; error: string | null; used_calls: number; budget_calls: number; conversation_id: string | null; sign_in_url?: string | null; created_at: string; finished_at: string | null }
export interface CoordinatorResponse { task_run_id?: string | null; reply: string; request_id: string; session_id: string; model: string; questions: string[]; crew: CrewInstance | null; draft: CrewInstance | null; crews?: CrewInstance[]; messages: Array<{role: "user" | "assistant"; content: string}>; conversation: Conversation | null }
export interface CoreStatus { configured: boolean; connected: boolean; provider: string; model: string | null; error: string | null }

export type AccessLevel = "off" | "read" | "write";
export interface PoolAction { operation: string; label: string; consequential: boolean; available: boolean; enabled_by_you: boolean }
export interface PoolResource {
  id: string; name: string; group: string; kind: "toggle" | "local" | "google" | "github" | "feeds";
  description: string; connected: boolean; account: string | null; level: AccessLevel; can_act: boolean;
  reads: string[]; readable: string[]; actions: PoolAction[];
}
export interface CommunityResource { id: string; name: string; license: string; home: string; adds: string; data_sent: string; status: "not_installed"; note: string }
export interface Playbook { id: string; title: string; example: string; crew: { name: string; does: string; needs: string[]; trigger: string }[]; edge_cases: string[] }
export interface OpenDataSource { id: string; name: string; group: string; gives: string; sends: string; home: string }
export interface ResourcePool { resources: PoolResource[]; community: CommunityResource[]; playbooks: Playbook[]; open_data?: OpenDataSource[] }
export interface TimelineEvent { at: string; kind: "reminder" | "review" | "run"; crew_id?: string; crew_name: string; title: string; uses_model?: boolean; status?: string }
export interface Timeline { generated_at: string; days: number; upcoming: TimelineEvent[]; standing: { crew_id: string; crew_name: string; label: string }[]; recent: TimelineEvent[] }
export interface CrewGraphData { nodes: { id: string; name: string; template_id: string; status: string; state: string; running: boolean; trigger_type: string; group: string | null }[]; edges: { from: string; to: string }[] }

/** Work in a line of agents that waits for the person: handed to an agent that asks first
 *  ("approve"), or a reviewer and its maker who could not agree in the rounds allowed. */
export interface HeldWork { kind: "approve" | "review_stuck" | "ask"; from: string; from_id?: string | null; at: string; result: string; files?: string[] | null; review?: string | null }

/** "run": the AI calls one run may make; "day": the ones it may make in a day. */
export interface CallLimit { kind: "run" | "day"; current: number; suggested: number }
