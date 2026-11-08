# Crew template SDK direction

The current engine supports persistent Run/Stop workers, type-specific bounded work, per-instance checkpoints, daily reminders, Computer metrics, Google resource-event workers, and policy-controlled actions. File access and rich long-term memory remain planned.

A template is a validated, versioned manifest describing purpose, supported capabilities, compatible resource types, safe default permissions, suggested triggers, and configuration schema. A template is not a running agent.

A Crew instance binds a template version to its own identity, instructions, grants, subscriptions, autonomy mode, model policy, budget, routines, goals, and isolated memory namespace. Multiple instances of one template never share memory or authorization implicitly.

```python
class CrewTemplateManifest(BaseModel):
    id: str
    version: str
    name: str
    purpose: str
    capabilities: list[str]
    compatible_resources: list[str]
    default_permissions: list[PermissionProposal]
    suggested_triggers: list[TriggerProposal]
```

Natural-language Chat and Create Crew use the publisher-funded coordinator and return validated `CoordinatorDecision` objects. `crew_proposal` creates or refines a persisted draft, with no model-granted access. It is excluded from `/crew`, counts, and delegation until a user selects Verify & add and deterministic setup checks pass. Questions persist in `configuration_plan`; unavailable agent connections or access prevent activation. `POST /core/turn` accepts an identity-bound request ID and persistent planning session. Multiple Core chats have independent session IDs, histories and planning-Crew pointers; desktop chat requests also supply their bound `conversation_id`. Builder sessions refine Crew independently without creating extra chat history. Deleting a chat does not delete its Crew, including unpublished drafts; the Crew page provides explicit resume controls.

`POST /crew/{id}/access` saves only requested Google capabilities, local action rules, notifications/device grants, and an agent connection. Google OAuth consent remains separate. Ready Crew activate automatically; denied capabilities are supplied to the coordinator so it can revise the plan. Models cannot enable actions, grant themselves access, or install integrations. `POST /crew/{id}/reply` supplies a user answer to the agent under its own model connection.

Setup is ordered: questionnaire first, access review second, Verify & add last. While setup questions remain, the card hides connections, resources, actions, notification choices, budgets, and verification controls, and defers loading access policies. The access endpoint rejects premature submissions with 409 without changing grants. Once the coordinator resolves all questions, the permission stage appears for the final draft.

Weather setup uses `requested_weather` in the validated draft and explicit `weather:true` in the user access request, producing `weather:read`. Advanced create/edit accepts `allow_weather`. Both this per-Crew grant and the global Weather switch are required; device Location is not. A worker can return up to three `weather_requests`, which are read-only lookups for any city/country or explicit destination coordinates. Intermediate actions/notifications are discarded. A second model call receives the actual forecasts and finishes the checkpoint. The runtime atomically reserves that call against daily/session budgets, extends its lease, rechecks grants, and refuses further lookup rounds. Weather-assisted work uses at most two model calls per worker step; other text steps use one. No forecast is proof of flight cancellation or delay.

For example, a travel Crew can combine calendar read access, Weather, desktop updates, and interval reviews to check Chennai rain before a flight. Calendar-change triggers alone do not detect changing weather. Forecast coverage is five days; missing dates, ambiguous destinations, and airline status require clarification or another supported source. Notification delivery requires its separate grant and a live Windows runtime.

Crew work runs as bounded jobs through the shared Model Router. Jobs have idempotency keys, leases, attempt limits, token/time budgets, and checkpoints. “Always on” means durable responsibility and resumability, never a continuously generating LLM process.

Current APIs: `POST /crew/{id}/run` accepts a message and returns the saved result or explicit failure; `GET /crew-runs?crew_id={id}` returns that instance's history for the local user. Each run consumes one model call. The backend refuses paused Crew, missing model grants, missing routes, and overlapping sessions. Template manifests distinguish `manual_assistance` from Schedule Crew's `daily_reminders`. Compatible resources and suggested triggers describe future support; neither grants access.

Persistent worker APIs: `POST /crew/{id}/start`, `/stop`, and `/check`. Manual advanced creation saves a stopped Crew. Description-based drafts publish and activate only after Verify & add passes all setup and access checks, subject to the global pause policy. `is_running` is durable user intent, while `runtime_state` distinguishes `waiting`, `working`, `needs_input`, `budget_paused`, `error`, and `stopped`. Work results use a validated checkpoint with `state`, `summary`, `next_step`, optional `question`, optional `notification`, and up to three typed `actions`; output cannot modify grants. The backend enforces review intervals, reserves a daily call before inference, and fences work using generation and lease state. Computer monitoring and daily reminders use zero model calls. Editing instructions clears the previous checkpoint and restarts bounded work if running intent is still enabled.

Trigger types are `manual`, `interval`, `daily`, `gmail_message`, `calendar_changed`, and `tasks_changed`. Resource workers use `waiting_event` when listening without inference. Each requires an explicit matching `google:<service>:read` grant and an authorized account. Sender/subject filters are deterministic; a resource refresh does not automatically activate a model. Per-Crew cursors and pending event snapshots persist; existing work leases and daily limits bound inference. The model route may receive authorized event data only after the user reviews that grant.

Drive metadata and Contacts add `google:drive:read` and `google:contacts:read`. They use interval/daily/manual reviews, not change triggers. Crew sessions receive at most ten entries per authorized Google resource; metadata access cannot read document contents or edit contacts. Public feeds use `feeds:arxiv-ai:read` and `feeds:bbc-tech:read`, enabled globally in Resources and separately selected per Crew. Connected sources are suggested to the coordinator but never automatically granted. The worker rechecks saved grants and runtime generation after collection before sending data to its model. [Resource and cost coverage](plugins/SETUP.md).


Local tools use exact `action:<operation>` grants and a separate scoped policy per Crew. Access changes invalidate prior execution authority by revision. Automatic followups stop at the configured session model-call limit and retain daily reservations across restarts. The model cannot enable actions or change access settings. See [scoped actions](decisions/0010-scoped-actions-and-desktop-presence.md).

Questions pause work in `needs_input` without polling the model. Answers resume under the same Crew's grants. Desktop updates require `notifications:show`, are deduplicated per event cursor, and are suppressed after grant revocation. Models must use the top-level `notification` field to request delivery; prose in a result is not an actual notification.

### Managed browser and saved defaults

Computer plans may include `device_monitor`: `{kind:"storage", drive:"C:", notifications:"each_check", min_free_gb:5}`. Kinds are system/storage; notifications are alerts/each_check/changes. This is a validated read-only configuration, not an authorization. The instance still needs device:read and notifications:show separately. Manual create/update accepts device_monitor and allow_notifications. Configuration uses existing JSON storage without destructive migrations. Workers publish actual OS counters with zero model calls; each-check/changed reports supersede older unread status reports, while threshold alerts use hysteresis. Durable updates expand the desktop icon, with user-controlled Check now/Stop and explicit dismissal.

After the questionnaire, new description-based drafts copy enabled action defaults only for requested operations that lack an explicit Crew setting. Modes and scopes are preserved exactly; inherited limits insufficient for the proposed task block verification. Google resource grants are never copied. `GET /action-policies/{operation}/crew` lists named independent settings; `PUT /action-policies` edits one owner. Existing Crew do not follow later default changes.

`POST /crew/{id}/browser-session` takes a validated HTTPS `url` and requires saved browser.open access. It pauses/fences work and executes the normal audited action, holding the separate per-Crew Edge window for up to 15 minutes for manual sign-in. `GET` reports actual window/hold state. Resume uses the persistent profile, not Chrome. Password/cookie exports and automatic password filling are not supported. Exact host restrictions still cover authentication/CDN requests.

Browser tools are requested through typed checkpoint `actions`, not provider-native tool calls. Open/click/fill return metadata; browser.read returns untrusted text/elements. A worker can make one budgeted corrective call for false missing-tool output (at most two calls per step, shared with the weather limit). Browser ownership contention uses `waiting_browser` without reserving model usage. End an ordinary monitoring cycle with `state: "wait_for_next_review"`; this resets bounded follow-on steps and waits the configured interval. Supply stable `notification_ids` with a top-level `notification` to suppress already-reported listings. Identifiers persist in the Crew's own observation, capped at 1,000. Full-cycle budgets must include reading and analysis, not just the number of scheduled reviews.
