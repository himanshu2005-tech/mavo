# 0018: Remove the Microsoft account connector

Status: Accepted. Supersedes the Microsoft-specific parts of ADR 0017.

The product owner requested complete removal after publisher tenant setup blocked testing. Keep Google Workspace, GitHub, weather/location and local tools. Do not replace working Google identity or require an Azure directory.

Remove Microsoft UI, configuration, publisher bundle fields, OAuth callbacks, native authorization URL allowance, capabilities, tools, schemas, coordinator guidance and HTTP execution. Unknown/retired capabilities fail validation and retired connection routes are unavailable. Windows and its C++ build tools remain required desktop infrastructure, not account connectors.

Before workers resume, idempotently delete the exact obsolete credential/metadata/policies, strip retired grants and draft requirements, reject pending/executing actions, and stop affected Crew with a setup message. Preserve Crew identity, responsibilities, chat, routes, unrelated grants/connections and completed audit history. No destructive database migration or automatic provider substitution. Remote account grants are not revoked by deleting local credentials.

Regression tests cover absence from catalogs, rejected routes/actions/capabilities, cleanup idempotency and preservation of Google/GitHub. Replace Microsoft-only tests with meaningful tests of the retained providers rather than keeping unreachable implementation code.
