from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from mavo.connectors.catalog import (
    ConnectorAccess,
    action_grant,
    provider_for,
    validate_capabilities,
)
from mavo.daily_triggers import DailyTrigger, unique_daily_triggers
from mavo.google_permissions import GoogleCapability
from mavo.resources.device_config import (
    DeviceMonitor,
    clean_threshold,
    drain_threshold,
    legacy_monitor,
    wants_recycle,
)
from mavo.task_plan import TaskPlan

ProviderId = Literal["ollama", "openai", "anthropic", "openrouter", "airouter"]
AutonomyMode = Literal["reactive", "proactive", "continuous"]
CrewStatus = Literal["active", "paused"]
TriggerType = Literal[
    "manual", "interval", "daily", "gmail_message", "calendar_changed", "tasks_changed"
]
GoogleResource = Literal["gmail", "calendar", "tasks", "drive", "contacts", "youtube"]


class ModelInfo(BaseModel):
    name: str
    size: int | None = None
    modified_at: datetime | None = None


class ProviderStatus(BaseModel):
    id: ProviderId
    name: str
    local: bool = False
    configured: bool
    connected: bool
    url: str
    models: list[ModelInfo] = []
    default_model: str | None = None
    error: str | None = None


class AppStatus(BaseModel):
    service: Literal["mavo"] = "mavo"
    version: str
    database: Literal["connected"] = "connected"
    providers: list[ProviderStatus]


class MessageView(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    model: str | None
    provider: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationView(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageView] = []

    model_config = {"from_attributes": True}


class ChatRequest(BaseModel):
    proposals_only: bool = False
    message: str = Field(min_length=1, max_length=32_000)
    route_id: str = Field(min_length=1, max_length=80)
    conversation_id: str | None = None

    @field_validator("message")
    @classmethod
    def non_whitespace(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty")
        return value


class ConversationRename(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    title: str = Field(min_length=1, max_length=160)


class ChatResponse(BaseModel):
    conversation: ConversationView
    assistant_message: MessageView
    crew_proposals: list[dict] = []
    action_requests: list[dict] = []


class ProviderConfigurationRequest(BaseModel):
    api_key: SecretStr = Field(min_length=8, max_length=512)
    default_model: str = Field(min_length=1, max_length=160)

    @field_validator("default_model")
    @classmethod
    def valid_default_model(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Invalid model name")
        return value


class ProviderConfigurationView(BaseModel):
    provider: ProviderId
    default_model: str
    has_credential: bool


class ModelRouteCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: ProviderId
    model: str = Field(min_length=1, max_length=160)
    api_key: SecretStr | None = Field(default=None, min_length=8, max_length=512)

    @field_validator("name", "model")
    @classmethod
    def strip_route_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Invalid route value")
        return value


class ModelRouteUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    provider: ProviderId | None = None
    model: str | None = Field(default=None, min_length=1, max_length=160)
    api_key: SecretStr | None = Field(default=None, min_length=8, max_length=512)

    @model_validator(mode="before")
    @classmethod
    def reject_null_values(cls, values: object) -> object:
        if isinstance(values, dict) and any(
            key in values and values[key] is None for key in ("name", "provider", "model")
        ):
            raise ValueError("Route name, provider, and model cannot be null")
        return values

    @field_validator("name", "model")
    @classmethod
    def strip_optional_route_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Invalid route value")
        return value


class ModelRouteView(BaseModel):
    id: str
    name: str
    provider: ProviderId
    model: str
    has_credential: bool
    created_at: datetime
    updated_at: datetime


class CrewTemplateManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    name: str
    purpose: str
    supported_capabilities: list[str]
    compatible_resources: list[str]
    default_permissions: list[str]
    suggested_triggers: list[str]
    implementation_status: Literal["manual_assistance", "daily_reminders"] = "manual_assistance"
    behavior: str = ""
    default_interval_minutes: int = 30


class CrewCreateRequest(ConnectorAccess):
    daily_triggers: list[DailyTrigger] = Field(default_factory=list, max_length=24)
    _daily_unique = field_validator("daily_triggers")(unique_daily_triggers)
    template_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    responsibilities: str = Field(min_length=1, max_length=4_000)
    autonomy_mode: AutonomyMode = "reactive"
    route_id: str | None = Field(default=None, min_length=1, max_length=80)
    max_model_calls: int = Field(default=5, ge=1, le=50)
    schedule_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    reminder_text: str | None = Field(default=None, max_length=1_000)
    review_interval_minutes: int = Field(default=30, ge=5, le=10080)
    daily_call_limit: int = Field(default=12, ge=1, le=100)
    allow_device_metrics: bool = False
    allow_notifications: bool = False
    device_monitor: DeviceMonitor | None = None
    allow_weather: bool = False
    trigger_type: TriggerType | None = None
    google_resources: list[GoogleResource] = Field(default_factory=list, max_length=6)
    google_capabilities: list[GoogleCapability] = Field(default_factory=list, max_length=10)
    mail_sender: str | None = Field(default=None, max_length=160)
    mail_subject: str | None = Field(default=None, max_length=160)

    @field_validator("name", "responsibilities")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @field_validator("schedule_time")
    @classmethod
    def valid_schedule_time(cls, value: str | None) -> str | None:
        if value is not None:
            hour, minute = (int(part) for part in value.split(":"))
            if hour > 23 or minute > 59:
                raise ValueError("Invalid schedule time")
        return value


class CrewUpdateRequest(BaseModel):
    # User-selected cloud fallback, never inferred by a model. Null disables it.
    fallback_route_id: str | None = Field(default=None, min_length=1, max_length=80)
    daily_triggers: list[DailyTrigger] | None = Field(default=None, max_length=24)
    _daily_unique = field_validator("daily_triggers")(unique_daily_triggers)
    connector_capabilities: list[str] | None = Field(default=None, max_length=16)
    _known_connector_capabilities = field_validator("connector_capabilities")(
        lambda v: validate_capabilities(v) if v is not None else None
    )
    name: str | None = Field(default=None, min_length=1, max_length=80)
    responsibilities: str | None = Field(default=None, min_length=1, max_length=4_000)
    autonomy_mode: AutonomyMode | None = None
    route_id: str | None = Field(default=None, min_length=1, max_length=80)
    max_model_calls: int | None = Field(default=None, ge=1, le=50)
    status: CrewStatus | None = None
    schedule_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    reminder_text: str | None = Field(default=None, max_length=1_000)
    review_interval_minutes: int | None = Field(default=None, ge=5, le=10080)
    daily_call_limit: int | None = Field(default=None, ge=1, le=100)
    allow_device_metrics: bool | None = None
    allow_temp_cleanup: bool | None = None  # free space: old temp files (and the Recycle Bin)
    allow_notifications: bool | None = None
    device_monitor: DeviceMonitor | None = None
    allow_weather: bool | None = None
    trigger_type: TriggerType | None = None
    google_resources: list[GoogleResource] | None = Field(default=None, max_length=6)
    google_capabilities: list[GoogleCapability] | None = Field(default=None, max_length=10)
    mail_sender: str | None = Field(default=None, max_length=160)
    mail_subject: str | None = Field(default=None, max_length=160)
    # Its place in a line of agents (pipelines.py).
    upstream: list[str] | None = Field(default=None, max_length=4)
    review: bool | None = None
    approve_first: bool | None = None
    max_rounds: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="before")
    @classmethod
    def reject_null_values(cls, values: object) -> object:
        required = (
            "name",
            "responsibilities",
            "autonomy_mode",
            "route_id",
            "max_model_calls",
            "status",
            "review_interval_minutes",
            "daily_call_limit",
            "allow_device_metrics",
            "allow_notifications",
            "allow_weather",
            "trigger_type",
            "google_resources",
        )
        if isinstance(values, dict) and any(
            key in values and values[key] is None for key in required
        ):
            raise ValueError("Required Crew fields cannot be null")
        return values

    @field_validator("name", "responsibilities")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @field_validator("schedule_time")
    @classmethod
    def valid_optional_schedule_time(cls, value: str | None) -> str | None:
        if value is not None:
            hour, minute = (int(part) for part in value.split(":"))
            if hour > 23 or minute > 59:
                raise ValueError("Invalid schedule time")
        return value


class HandoverDecision(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    approve: bool
    note: str = Field(default="", max_length=1000)


class CrewView(BaseModel):
    fallback_route_id: str | None = None
    daily_triggers: list[DailyTrigger] = Field(default_factory=list)
    question: str | None = None
    # Work handed over that waits for the person's OK: approve it, or send it back.
    held: dict | None = None
    # An AI-call limit that stopped its last run, with a limit that would let it finish.
    limit: dict | None = None
    # A once-a-day run skipped because Mavo was not running at its time.
    skipped_run: dict | None = None
    # The last run its local model could not do, done instead on a cloud route.
    fallback: dict | None = None
    configuration_plan: dict = Field(default_factory=dict)
    id: str
    template_id: str
    name: str
    responsibilities: str
    autonomy_mode: AutonomyMode
    status: CrewStatus | Literal["draft"]
    provider: ProviderId | Literal["unassigned"]
    model: str
    route_id: str | None
    max_model_calls: int
    schedule_time: str | None
    reminder_text: str | None
    resources: list[str]
    permissions: list[str]
    triggers: list[str]
    created_at: datetime
    updated_at: datetime
    is_running: bool
    runtime_state: str
    review_interval_minutes: int
    daily_call_limit: int
    daily_calls_used: int
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_runtime_error: str | None
    checkpoint: str | None
    trigger_type: TriggerType
    mail_sender: str | None
    mail_subject: str | None

    model_config = {"from_attributes": True}


class ReminderView(BaseModel):
    kind: str = "reminder"
    id: str
    crew_id: str
    crew_name: str
    message: str
    scheduled_for: datetime
    local_date: str
    status: str
    task_run_id: str | None = None
    task_step_id: str | None = None

    model_config = {"from_attributes": True}


class CrewRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)

    @field_validator("message")
    @classmethod
    def non_empty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe what you want this Crew to do")
        return value.strip()


class CrewRunView(BaseModel):
    id: str
    crew_id: str
    crew_name: str
    route_name: str
    provider: str
    model: str
    prompt: str
    status: Literal["running", "completed", "failed", "interrupted", "cancelled"]
    result: str | None
    error: str | None
    model_calls: int
    created_at: datetime
    finished_at: datetime | None
    kind: str

    model_config = {"from_attributes": True}


class RouteTestView(BaseModel):
    route_id: str
    model: str
    response: str
    latency_ms: int


class WeatherCurrent(BaseModel):
    temperature_c: float
    apparent_temperature_c: float
    precipitation_mm: float
    wind_speed_kmh: float
    weather_code: int
    is_day: bool


class WeatherDay(BaseModel):
    date: str
    weather_code: int
    temperature_max_c: float
    temperature_min_c: float
    precipitation_probability_max: int
    sunrise: str
    sunset: str


class WeatherSnapshot(BaseModel):
    provider: Literal["open-meteo"] = "open-meteo"
    location: str
    latitude: float
    longitude: float
    timezone: str
    fetched_at: datetime
    cached: bool
    current: WeatherCurrent
    forecast: list[WeatherDay]


class WeatherLocation(BaseModel):
    source: Literal["manual", "device"] = "manual"
    accuracy_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    id: int | None = None
    name: str = Field(min_length=1, max_length=160)
    region: str = ""
    country: str = ""
    country_code: str = ""
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    timezone: str | None = None


class GoogleClientRequest(BaseModel):
    client_id: str = Field(
        pattern=r"^[a-zA-Z0-9_-]+\.apps\.googleusercontent\.com$", max_length=255
    )
    client_secret: SecretStr | None = Field(default=None, max_length=512)


class GoogleSignInRequest(BaseModel):
    model_config = {"extra": "forbid"}
    resources: list[GoogleResource] = Field(default_factory=list, max_length=6)
    all_services: bool = False
    capabilities: list[GoogleCapability] = Field(default_factory=list, max_length=10)


class SuggestedAction(BaseModel):
    model_config = {"extra": "forbid"}
    operation: str
    reason: str = Field(default="", max_length=300)
    folders: list[str] = Field(default_factory=list, max_length=8)
    domains: list[str] = Field(default_factory=list, max_length=20)
    apps: list[str] = Field(default_factory=list, max_length=20)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    repositories: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("operation")
    @classmethod
    def supported_action(cls, value):
        from mavo.action_rules import CATALOG

        if value not in CATALOG:
            raise ValueError("Unsupported action")
        return value


class CrewDraft(BaseModel):
    daily_triggers: list[DailyTrigger] = Field(default_factory=list, max_length=24)
    _daily_unique = field_validator("daily_triggers")(unique_daily_triggers)
    setup_questions: list[str] = Field(default_factory=list, max_length=3)
    # Names of Crew in the same batch (or existing Crew IDs/names) whose finished work
    # this Crew receives. Forms the Crew graph; cycles are rejected by the planner.
    depends_on: list[str] = Field(default_factory=list, max_length=4)
    review: bool = Field(
        default=False,
        description="This Crew REVIEWS the work it receives from its depends_on Crew: it ends with "
        "'VERDICT: pass' (the work goes on) or 'VERDICT: fix' (sent back to be done again).",
    )
    approve_first: bool = Field(
        default=False,
        description="Work handed to this Crew waits for the person's OK before it starts. Use for "
        "a step that publishes, sends, uploads or cannot be undone.",
    )
    max_rounds: int = Field(
        default=3,
        ge=1,
        le=5,
        description="For a reviewer: how many times it may send work back before the "
        "person decides.",
    )
    leads_team: bool = Field(
        default=False,
        description="This Crew LEADS a team: on its schedule it decides step by step which member "
        "works next and on what, sends work back to be fixed, asks the person when a decision is "
        "theirs, and finishes with a summary. It does no work and needs no tools itself.",
    )
    lead: str | None = Field(
        default=None,
        max_length=80,
        description="Name of the Crew in this batch that leads this Crew's team. A "
        "member works only "
        "when its lead gives it a task, and reports back to the lead. Null if not in a team.",
    )
    # When the job ends, if it does: a last day (it stops after that day), and/or an outcome that
    # finishes it ("a guest is booked"). Both empty: it keeps going until the person stops it.
    run_until: date | None = Field(
        default=None,
        description="Last day (YYYY-MM-DD) for a job that ends: the person's end, or a sensible "
        "one for a hunt (a few weeks). Null only for a lasting duty (news, inbox, reminders).",
    )
    goal: str | None = Field(
        default=None,
        max_length=200,
        description="For a Crew working toward an outcome, that outcome in a few words ('a guest "
        "has agreed and the recording is in my calendar'); it stops itself once met. Every "
        "Crew of one outcome shares it. Null for a lasting duty.",
    )
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    template_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    responsibilities: str = Field(min_length=1, max_length=4000)
    route_id: str | None = Field(default=None, max_length=80)
    requested_actions: list[SuggestedAction] = Field(default_factory=list, max_length=10)
    requested_notifications: bool = False
    requested_device_metrics: bool = False
    device_monitor: DeviceMonitor | None = None
    requested_weather: bool = False
    max_model_calls: int = Field(default=5, ge=1, le=20)
    trigger_type: TriggerType = "manual"
    schedule_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    review_interval_minutes: int = Field(default=30, ge=5, le=10080)
    daily_call_limit: int = Field(default=12, ge=1, le=100)
    requested_google_resources: list[GoogleResource] = []
    requested_google_capabilities: list[GoogleCapability] = []
    requested_connector_capabilities: list[str] = Field(default_factory=list, max_length=16)
    _known_connector_capabilities = field_validator("requested_connector_capabilities")(
        validate_capabilities
    )
    mail_sender: str | None = Field(default=None, max_length=160)
    mail_subject: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def supported_resource_requirements(self):
        if self.daily_triggers:
            if self.trigger_type not in {"manual", "daily"}:
                raise ValueError("Daily reminder triggers cannot mix with other trigger types")
            self.trigger_type = "daily"
            self.schedule_time = self.daily_triggers[0].time
        if self.trigger_type == "daily" and self.template_id != "schedule" and self.schedule_time:
            # Work at a time of day ("every morning at 8, the top AI news"): a Crew that reviews
            # once a day at that time. A daily trigger is otherwise a plain reminder, and this
            # request was refused outright.
            self.trigger_type, self.daily_triggers = "interval", []
            self.review_interval_minutes = 1440
        if self.trigger_type == "daily":
            if self.template_id != "schedule":
                raise ValueError("Daily reminder triggers require Schedule Crew")
            if (
                self.requested_actions
                or self.requested_google_resources
                or self.requested_google_capabilities
                or self.requested_connector_capabilities
                or self.requested_weather
                or self.requested_device_metrics
            ):
                raise ValueError("Daily reminders deliver text only; use reviews for AI tools")
        self.requested_connector_capabilities = validate_capabilities(
            [
                *self.requested_connector_capabilities,
                *[
                    action_grant(a.operation)
                    for a in self.requested_actions
                    if provider_for(a.operation) not in {"local", "google"}
                ],
            ]
        )
        # Device collection is implemented only by Computer Crew. Local clock
        # information is already supplied to planning and requires no device grant.
        if self.template_id != "computer":
            self.requested_device_metrics = False
            self.device_monitor = None
        else:
            # A Computer Crew reads the device itself: that is all it does. "Battery Watch" was
            # drafted without device metrics (and with terminal commands it never runs), was
            # added, and sat blocked: "Allow device metrics in Edit".
            self.requested_device_metrics = True
            self.requested_actions = [
                a for a in self.requested_actions if not a.operation.startswith("terminal.")
            ]
            if self.device_monitor is None:
                self.device_monitor = legacy_monitor(self.responsibilities)
            if (
                self.device_monitor.kind == "system"
                and self.device_monitor.battery_drain_per_hour is None
            ):
                drain = drain_threshold(self.responsibilities)
                if drain:
                    self.device_monitor = self.device_monitor.model_copy(
                        update={"battery_drain_per_hour": drain}
                    )
            if self.device_monitor.clean_temp_days is None:
                days = clean_threshold(self.responsibilities)
                if days:
                    self.device_monitor = self.device_monitor.model_copy(
                        update={
                            "clean_temp_days": days,
                            "empty_recycle_bin": wants_recycle(self.responsibilities),
                        }
                    )
        # An action is a check and then a follow-up that does it: a daily limit of 2 left the
        # shutdown at its check, out of budget, every day.
        if self.requested_actions and self.daily_call_limit < self.max_model_calls + 1:
            self.daily_call_limit = min(100, self.max_model_calls + 1)
        required = {
            "gmail_message": "gmail:read",
            "calendar_changed": "calendar:read",
            "tasks_changed": "tasks:read",
        }.get(self.trigger_type)
        capabilities = set(self.requested_google_capabilities)
        capabilities.update(
            action_grant(action.operation)[7:]
            for action in self.requested_actions
            if provider_for(action.operation) == "google"
        )
        if required:
            capabilities.add(required)
        if "gmail:body" in capabilities:
            capabilities.add("gmail:read")
        self.requested_google_capabilities = sorted(capabilities)
        return self

    @model_validator(mode="before")
    @classmethod
    def omitted_optional_settings(cls, value: object) -> object:
        # Some providers emit null for irrelevant optional settings. Treat only
        # these safe defaults as omitted, then run the normal bounded validation.
        if isinstance(value, dict):
            defaults = {
                "review_interval_minutes",
                "daily_call_limit",
                "requested_google_resources",
                "requested_google_capabilities",
            }
            return {
                key: item for key, item in value.items() if item is not None or key not in defaults
            }
        return value


class CrewLink(BaseModel):
    """One of the person's Crew passing what it finds to another ("@Inbox Summary, when you find a
    job email, tell @Placements Job Watch"): from the one that tells to the one that hears."""

    model_config = {"extra": "forbid"}
    from_crew_id: str = Field(min_length=1, max_length=80)
    to_crew_id: str = Field(min_length=1, max_length=80)


class CoordinatorDecision(BaseModel):
    crew_proposals: list[CrewDraft] = Field(default_factory=list, max_length=8)
    model_config = {"extra": "forbid"}
    reply: str = Field(min_length=1, max_length=8000)
    crew_proposal: CrewDraft | None = None
    questions: list[str] = Field(default_factory=list, max_length=3)
    target_crew_id: str | None = Field(default=None, max_length=80)
    delegate_to: str | None = Field(default=None, max_length=80)
    instruction: str | None = Field(default=None, max_length=4000)
    # A job to do now, once (see task_plan.py), instead of a lasting Crew.
    task: TaskPlan | None = None
    # Existing Crew to connect, so one's finished results reach the other.
    connect: list[CrewLink] = Field(default_factory=list, max_length=4)
    # Lasting facts the person just told about themselves: kept in Mavo's shared memory.
    remember: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="before")
    @classmethod
    def target_in_its_place(cls, data):
        """A real planner put target_crew_id inside the Crew it was refining, twice, and the
        turn failed ("Extra inputs are not permitted"). It belongs beside the proposal: moved
        there for one proposal, dropped from a batch (a batch makes new Crew)."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        one = data.get("crew_proposal")
        if isinstance(one, dict) and "target_crew_id" in one:
            one = dict(one)
            inner = one.pop("target_crew_id")
            data["crew_proposal"] = one
            if inner and not data.get("target_crew_id"):
                data["target_crew_id"] = inner
        if isinstance(data.get("crew_proposals"), list):
            data["crew_proposals"] = [
                {k: v for k, v in p.items() if k != "target_crew_id"} if isinstance(p, dict) else p
                for p in data["crew_proposals"]
            ]
        return data

    @model_validator(mode="after")
    def single_operation(self):
        # A job now AND the Crew that carry it on ("suggest guests now, and tell me when one
        # replies") is one request: a task may come with new Crew. Not with a question (the task
        # would start before the answer), a delegation, or a change to an existing Crew.
        if self.task and (
            self.connect or self.target_crew_id or self.delegate_to or self.questions
        ):
            raise ValueError(
                "A task runs now: it cannot also delegate, change a Crew or ask a question"
            )
        if self.crew_proposal and self.crew_proposals:
            raise ValueError("Use either one proposal or a batch, not both")
        if self.crew_proposals and self.questions:
            raise ValueError("Ask questions first; propose a Crew batch once they are answered")
        if self.crew_proposals and self.target_crew_id:
            raise ValueError("Batch proposals create new Crew; refine existing Crew one at a time")
        if self.delegate_to and (self.crew_proposal or self.crew_proposals or not self.instruction):
            raise ValueError("Delegation needs an instruction and cannot also create a Crew")
        return self


class CoordinatorRequest(BaseModel):
    model_config = {"extra": "forbid"}
    message: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    session_id: str = Field(default="main", pattern=r"^(?:main|[0-9a-fA-F-]{36})$")
    conversation_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    crew_id: str | None = Field(default=None, max_length=80)

    @field_validator("message")
    @classmethod
    def meaningful_message(cls, value):
        if not value.strip():
            raise ValueError("Describe what you need")
        return value.strip()


class AcceptCrewProposal(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}
    name: str | None = Field(default=None, min_length=1, max_length=80)
    approved_google_resources: list[GoogleResource] = []
    approved_google_capabilities: list[GoogleCapability] = []
    allow_device_metrics: bool = False
