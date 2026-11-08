# 0013: Destination weather and service-specific Google consent

Status: Accepted

Weather access means forecasts for any public destination; device Location only controls collecting the user's coordinates. The UI preview does not constrain agent queries. Weather work needs both the global switch and a per-Crew user grant. The coordinator proposes this need but cannot authorize it.

Workers may request at most three typed read-only weather lookups and one budget-reserved model follow-up. No intermediate action or notification is executed. Exact city matching avoids airport/prefix confusion; ambiguous cities remain unresolved. Forecast errors and five-day coverage are explicit. Session and daily limits, leases, and generation/permission checks bound work; revocation stops it. Weather data alone cannot verify airline operations.

Google Workspace keeps Connect all while offering individual Gmail, Calendar, and Tasks capability selection. Google consent is additive; the UI does not misrepresent unchecked boxes as revocation. Per-Crew grants and action policies remain separate. Additional Google integrations require real adapters and release testing, not merely enabling another Cloud API.

Policy revision identifiers are server-owned fencing metadata. The frontend explicitly serializes only editable policy fields for both Actions and Crew setup; strict backend validation remains intact.
