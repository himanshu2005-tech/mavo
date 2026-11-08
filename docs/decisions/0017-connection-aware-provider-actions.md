# 0017: Connection-aware account tools and optional providers

Status: Accepted for the shared connector architecture; Microsoft-specific decisions superseded by [ADR 0018](0018-remove-microsoft-connector.md). The descriptions below preserve the original decision, not current Microsoft support.

Google login is not Google Workspace consent. A registered action catalog must not look like a list of working connected tools. Actions now excludes disconnected providers and operations without matching returned account scopes; worker schemas apply the same rule plus exact Crew grants and saved action policy. Setup can request a connection without pretending that it already exists. The coordinator prefers connected providers and asks about provider choice rather than assuming Google.

Keep the existing Google integration compatible and add a bounded connector hub for Microsoft Graph and selected GitHub repositories. Microsoft uses public-client Authorization Code + PKCE, one-use state, a bounded loopback callback, account identity from fixed Graph /me, secure refresh tokens, returned scopes and a publisher-owned public client ID. GitHub initially uses a user-supplied fine-grained token constrained to selected repositories; GitHub App OAuth remains planned.

New actions use the existing typed argument, permission, claim, revision, generation, audit and no-replay engine. Namespaced grants prevent one provider's consent from authorizing another. Disconnection/redaction is provider-scoped; in particular Google disconnect must not redact Microsoft/GitHub action history. Provider rejections remove unavailable capabilities. Reading a resource never grants a write action.

Account metadata lives in existing namespaced WorkspaceSetting JSON and secrets in the existing credential store, requiring no destructive schema migration. Reads and aggregate worker collection have explicit time bounds; leases renew at bounded inference/action phase boundaries. Microsoft/GitHub interval review support is distinct from Google's meaningful-event triggers. SDK registration remains static and publisher-controlled, not executable plugins installed by a model.

Google gains draft creation through its drafts endpoint, never the send endpoint. Microsoft send acknowledgement is not delivery confirmation. GitHub issues/comments stay within connected repository selection even when custom action limits are removed.
