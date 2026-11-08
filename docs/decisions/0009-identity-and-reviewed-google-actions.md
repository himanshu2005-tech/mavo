# Mandatory identity and reviewed Google actions

Status: accepted.

Firebase Google login identifies the workspace owner. The desktop uses a system-browser PKCE flow and verifies Google identity before exchanging it with Firebase. Firebase tokens are independently verified against fixed Google signing certificates, the configured project, Google provider, and verified account identity. Tokens use Windows DPAPI storage. A publisher using different Google and Firebase projects must allow its existing desktop client ID in Firebase once. Public users only click Sign in.

The backend enforces both the launch bearer token and Firebase session on workspace endpoints. First login binds the local database to that Firebase UID and project. Sign-out stops Crew, fences in-flight work, and rejects pending approvals. Connector consent is independent and survives sign-out. This does not introduce cloud workspace synchronization.

Google permissions use a server-owned capability catalog. Account OAuth grants and exact per-Crew grants are checked separately. The model can suggest permissions and validated actions but cannot grant permissions or approve actions. A Gmail modify OAuth scope can imply send access at Google; a Crew with only the modify grant still cannot propose sending.

Every consequential Google action is a durable proposal reviewed by the user. Execution claims the proposal atomically, rechecks the account, Crew generation, grants, and session, then calls a fixed provider endpoint. Existing Calendar/Tasks mutations include an entity version. Provider acknowledgments and read-back checks determine outcomes. Mail send acknowledgment does not claim delivery. Ambiguous network/provider outcomes become unknown and are never automatically resent; expired execution leases also become unknown. This sacrifices automatic retries to avoid duplicate externally visible actions.

Google disconnection removes credentials and cached content, stops affected Crew, rejects pending actions, and redacts stored action payloads/results. SQLite audit content itself is not encrypted. Tests use temporary databases and mocked writes; release verification must separately cover the publisher's real OAuth project and consent.
