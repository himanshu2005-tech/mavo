# Automation verification

Latest Google/connector run (2026-11-02): **300 backend tests** and **34 frontend tests** passed, with Ruff, TypeScript/build, all-page dark/compact contrast checks and rebuilt sidecar authentication/chat/Crew/device/browser smoke checks. Native Google sign-in was completed manually in the rebuilt packaged app with no client secret present in desktop configuration. Gmail, Calendar and Tasks each accepted minimal authenticated read probes through the actual native API. Current grants are read-only; no real mail/calendar/task/issue writes were performed. The publisher token-exchange and refresh flow is covered with signed identities, allowlisted scopes/clients, PKCE/redirect/body/rate checks, no reflected/stored credentials and public-bundle privacy tests. GitHub PR/CI tests verify separate account/Crew grants, bounded metadata, caching, no body/log/artifact collection and no implicit writes. No real GitHub token was connected by these tests.

Latest multiple-chat run (2026-11-02): 273 backend tests and 33 frontend tests passed, with Ruff, TypeScript and the production frontend build. Nine chat API cases cover independent prompts/questionnaires, titles, exact idempotency, deletion, concurrent sessions, expired-session completion and authentication; two additional migration cases preserve all legacy history and repair empty revision metadata safely. Frontend coverage includes late responses after switching/logout and failed-message restoration to the correct composer. The native UI smoke passed New chat, rename, input isolation, switching, persisted empty history and confirmed deletion, preserving existing chats without model calls. Normal/compact dark-layout checks passed on all eight pages. The rebuilt sidecar passed the login gate and packaged chat lifecycle smoke tests.

The tests use disposable Crew and separate temporary SQLite databases. They never grant access to a personal inbox, send mail, change a real calendar/task, fill a disk, or run cleanup. Temporary test instances are removed when their workspace closes; the user's C-drive Crew and its permissions are preserved.

Latest connector-removal run (2026-11-02): 262 backend tests, 25 frontend tests/build, Ruff, and both Rust positioning tests passed. Live Resources/Actions checks confirm only Google/GitHub account connectors, masked token input, connected-only actions, and readable dark layouts at normal/compact sizes. The rebuilt packaged backend passed mandatory-login checks and isolated CRUD/Run/Stop/device/reminder checks, including real Edge open/read/close inside Tauri's Windows job boundary. No real mail, calendar, task, or issue writes were performed.

The earlier automation run passed all 15 live template smoke checks using 13 total AIRouter calls; these were not repeated for connector removal. Backend tests emit an upstream FastAPI/Starlette HTTP test-client deprecation warning; it does not fail the tests.

## Real runtime checks

- All 15 template Run/Stop smoke tests passed through the shared runtime. Thirteen text specialties used the saved AIRouter agent route with one bounded call each; Schedule and Computer used zero model calls. These prove bounded work, validation, persistence, and lifecycle—not unavailable specialty integrations.
- Installed Edge open/read/fill/click/close passed on a public test page and explicitly inserted test controls. No personal Chrome profile or site login was reused. An actual installed Node version command ran through the restricted command catalog without a shell.
- Real device and C-drive counters, notification acknowledgement/redelivery, daily reminder deduplication, and denied device access passed in isolated sample workspaces with no inference.
- Desktop checks cover dark layouts, readable contrast, attached messages, unread collapse/reopen, and native work-area bounds. Public release smoke tests exercise authentication and the packaged browser inside the same Windows process-tree job boundary as Tauri.

## Fault and lifecycle coverage

`test_automation_matrix.py` adds 22 scenarios alongside the existing regression suite:

- alerts, each-check, and changed-status reports, with notifications both enabled and denied;
- enabling notification access after silent collection;
- immediate revoke/regrant, without an intervening delivery poll;
- stopping, deleting, pausing, and revoking access during an in-flight counter read;
- repeated status coalescing and zero model usage for device checks;
- persistence of warning baselines across a runtime restart;
- provider/network errors, timeouts, malformed checkpoints, bounded retries, and no fabricated success notifications;
- scheduler concurrency capped at two, with a third Crew receiving a turn.

Existing tests also cover weather ambiguity/revocation/budgets, token/identity boundaries, OAuth state/PKCE and scope upgrades, Google event baselines/filtering/deduplication, revision-fenced actions, private-network rejection, duplicate execution claims, crash leases, deletion races, and draft/questionnaire/verification gates.

Two related delivery defects were found and fixed: collection without notification permission incorrectly suppressed later warnings/change reports, and a quick revoke/regrant could otherwise leave an old pending update eligible for delivery until a poll cancelled it. Collected counters and published-delivery baselines are now separate, and revocation cancels queued updates transactionally.

## Reproduce

Run `.\scripts\check.ps1` for lint, backend/frontend tests, and Tauri compilation. Run `backend\.venv\Scripts\python.exe scripts\verify-automation-examples.py` for isolated deterministic real-device examples. `scripts\verify-workers.py` uses the saved AIRouter route for all templates and incurs provider charges (13 bounded text calls); results are saved under ignored `.build`. Live desktop checks require temporary WebView debugging, which must be disabled afterward.

## Limits

No finite suite covers every condition. Real Google consent, private portal login/extraction, machine shutdown/suspend/resume, multiple physical monitors/DPI configurations, disk removal, and long-running days/weeks still require manual or dedicated-environment testing. Fault tests simulate those failures where safe; they do not claim to reproduce every OS/provider behavior. The runtime cannot execute while Windows is shut down or suspended. Firebase/Google service contract fixtures are not evidence that a particular user's cloud permissions remain valid.

During this run, one isolated packaged-browser request exceeded the smoke test's 75-second HTTP timeout; cleanup also timed out, and the test terminated its owned process tree. A fresh invocation passed open/read/close and the remaining packaged checks. The cause of the intermittent stall is not established; this remains a reliability follow-up, not a claimed fix. Source-runtime browser open/read/fill/click/close passed separately.
