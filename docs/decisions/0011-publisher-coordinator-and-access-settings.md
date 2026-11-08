# Publisher-funded coordinator and automatic Crew setup

Status: accepted. Supersedes the interaction and action-policy portions of ADRs 0009 and 0010 at the user's request. The single-chat restriction is superseded by [ADR 0019](0019-multiple-core-chats.md); publisher-funded routing remains unchanged.

Mavo has one normal chat. Its publisher-funded AIRouter Minimax model runs behind a separate Firebase-authenticated gateway. Desktop agent connections are used only for Crew jobs. The shared credential must never ship in a desktop bundle, including development installers. A local gateway enables testing before a public server exists; a release build requires a public HTTPS URL. This is the only server boundary introduced: workers and resource/tool execution remain local.

Planning returns a validated decision, not executable instructions. The backend persists a real Crew immediately and supports clarification, refinement, and delegation. Request IDs, durable session leases, stored payloads, and generation checks prevent duplicate creation and stale edits. Creation does not grant permissions. Missing setup answers, agent connections, OAuth scopes, or exact Crew grants prevent activation. When ready, the configured trigger starts automatically unless the user stopped it or globally paused work.

Users set each action to Off, Allow with limits, or Allow without limits. Enabled actions execute without a per-action approval screen. Models cannot change settings. Custom limits are optional only in unrestricted mode; platform schemas, fixed command catalog, public HTTPS-only browser transport, app input exclusions, exact resource grants, budgets, leases, and outcome checks remain mandatory. Existing ask-every-time policies migrate to Off; existing automatic scopes are preserved. Old approval endpoints return 410.

Crew can pause with a specific question and resume when the user answers. Agent-side follow-ups use the agent connection. Important updates use an explicit notification checkpoint field and permission, with durable event deduplication. No notification is inferred from prose or delivered after its grant is revoked.

Firebase Google sign-in remains mandatory, with no logout button in the product UI. Google resource disconnection remains available separately. A revoked/expired account still stops authenticated execution. Public rollout requires HTTPS deployment, Google verification, clear disclosure of coordinator cloud processing, provider spending limits, and installer signing; local testing alone does not satisfy those release requirements.
