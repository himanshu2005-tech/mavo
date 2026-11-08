# ADR 0004: Named model routes are the user-facing model boundary

## Status

Accepted.

## Decision

Chat and Crew select a named route rather than independently selecting a provider and model. Each route binds one provider, exact model ID, and an opaque credential identifier. Cloud credentials are separately protected with Windows DPAPI, allowing multiple routes for the same provider with different keys.

Existing single-provider settings are imported as legacy default routes. API keys are never returned to the frontend. Deleting a route assigned to a Crew is rejected until the Crew is changed or removed.

## Consequences

Users control the stable choices exposed to Chat and Crew, route names can express use cases, and models cannot silently grant themselves a different provider or credential. Provider discovery remains advisory; an exact model ID can be saved even during a temporary provider outage.
