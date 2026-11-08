# ADR 0024: simple connection cards and honest revocation

Status: accepted

Replace resource-level setup matrices with one Connect button per Google/GitHub account, concise visible access descriptions and expandable actual grants. Browser consent remains the authority; Crew grants and Actions settings are unchanged. Connected means saved account authorization, not a claim that every API or permission was granted.

Google connects the supported scope union in one consent flow. GitHub's native device flow remains secretless. Its normal path commits the provider-authorized repository selection automatically when one to ten repositories are available, using their common supported capabilities. If installation is needed, open GitHub and perform bounded 15-second discovery for up to five minutes. More than ten repositories require an explicit selection; never silently select a subset. No normal token forms, scope matrix, refresh or save step remains in Resources. Advanced API endpoints remain available to developer clients.

Add explicit revoke endpoints and non-secret durable receipts. Local credentials, scopes and affected work are removed/fenced before any remote revocation wait. Google uses its fixed HTTPS revoke endpoint with credentials only in the POST body, bounded timeout, no redirects/retries and no private provider response display. If no confirmation is received, report local removal and a manual provider step rather than a false remote success. Repeated revocation preserves existing confirmation.

GitHub grant deletion requires the publisher's client secret, which this device client does not possess. Do not distribute a secret, misuse exposed-credential revocation APIs or claim local deletion revoked the provider grant. Open GitHub's exact Authorized GitHub Apps page for the user to revoke, and record user confirmation distinctly from a provider-verified receipt. Reconnecting clears old receipts but does not restore removed Crew grants.

Tests cover simple auto-connect, pending installation, too many repositories, cancellation, connected/not-connected UI transitions, exact disclosures, explicit revoke, remote failure/no false success, repeated revocation, credential isolation and preserved Firebase login.
