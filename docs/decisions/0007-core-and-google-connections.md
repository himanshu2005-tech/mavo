# ADR 0007: one Core conversation and publisher-owned Google OAuth

Status: accepted; the one-conversation decision is superseded by [ADR 0019](0019-multiple-core-chats.md), and the no-broker assumption by [ADR 0020](0020-publisher-google-token-exchange.md). This record preserves the original decisions, not current chat/authentication requirements.

Mavo Core is one persistent conversation. It may propose a typed Crew configuration, which is stored separately from plain assistant text. Schema validation rejects arbitrary executable instructions and permissions. Accepting a proposal creates a stopped Crew in one transaction; explicit user grants, not the model, determine resource access. Legacy conversation history is retained.

Public users connect accounts through a button and browser consent. The publisher owns the Desktop OAuth client, whose public ID is bundled once at release time. Default builds do not ask end users to register applications. Developer client editing is opt-in and cannot override bundled publisher identity. No hosted token broker or Firebase dependency is required for this Windows flow.

Google authorization uses a short-lived random-port loopback listener, PKCE, state, nonce, and signed OpenID identity validation. Tokens use the credential-store interface and Windows DPAPI. Native clients explicitly request the union of selected service scopes on subsequent consent; they do not claim web incremental authorization support.

Gmail headers, primary calendar, and default tasks are read-only resource adapters. The shared cache and persisted domain events separate refreshes from meaningful work. Workers apply deterministic filters, durable cursors, existing leases, and budgets before inference. This narrow event capability does not replace the planned general Signals engine.

Google public verification and restricted Gmail scope review remain publisher release responsibilities. Full mailbox/calendar/task synchronization, writes, multiple accounts, and hosted execution are deferred.
