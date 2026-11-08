# Development

## Model map regression checks

Home uses radial agent nodes and one grouped Connections box; there is no workflow-board
switch. `scripts/check-model-map.py` checks both themes, resizing, selection, request signals,
and line alignment while the box scrolls or the map zooms. Identity-color unit tests cover
500 Crews plus resources, routes and actions without repeated RGB values in either theme.
Camera tests cover framing 2 through 500 agents. These are not a promise that every color
is perceptually distinguishable or that every label is readable in a dense overview.

Startup regression: with Vite on 1420, run
`backend\.venv\Scripts\python.exe scripts\check-startup.py`. It loads the real application
entry with isolated signed-out and signed-in API fixtures, then blocks the main module to
verify the startup recovery screen. It never signs out the real account. With temporary native
WebView debugging, `node scripts/check-startup-native.mjs` checks that the actual app shows
either login or the workspace, without printing account details. Disable debugging afterward.

Run `npm run check` for frontend tests/type checking/production build, and
`backend\.venv\Scripts\python.exe -m pytest backend` from the repository root for backend
tests. Shared test fixtures use the `tests` package so the same commands also work from backend.

For an isolated browser check, start `npx vite --host 127.0.0.1 --port 1432 --strictPort` in one
terminal, then run `backend\.venv\Scripts\python.exe scripts\check-model-map.py` in another.
It uses synthetic accounts and responses, checks both themes, ordered traces, cloud fallback
controls and reduced motion, and saves screenshots in `.build/model-map-check`. It does not
create real Crew, edit user permissions or spend provider credits. Stop that preview server
after testing. Real route tests from the model panel perform one inference on the selected route.

The browser check also injects run snapshots into an isolated store: no provider lights up
merely because a Crew is enabled, actual requests show progress, and fast completions stay
visible. Startup, palette persistence and reduced motion have separate regression coverage.

With temporary native WebView debugging enabled, `node scripts/check-native-map.mjs` checks
the real desktop map, loopback health and rejection of anonymous requests without changing
data. `node scripts/check-model-routes.mjs` reports configuration only; add `--live` explicitly
to send one short test on the first saved Ollama and AIRouter routes (billable on cloud).
Disable WebView debugging before leaving the application running.

## Prerequisites

Use Windows PowerShell with Python 3.12+, Node.js 22+, Rust stable-MSVC, WebView2, and Visual Studio Build Tools with the **Desktop development with C++** workload and a Windows 10 or 11 SDK. Ollama is optional when using a cloud provider. Confirm the command-line prerequisites with:

```powershell
py -3.12 --version
node --version
rustc --version
cargo --version
```

## Setup and launch

```powershell
.\scripts\setup.ps1
.\scripts\dev.ps1
```

`setup.ps1` creates `backend/.venv`, installs the backend in editable mode with development tools, and installs locked npm dependencies. `dev.ps1` starts `tauri dev`; Tauri starts and stops FastAPI itself.

If Windows reports `failed to remove ... mavo.exe: Access is denied`, an earlier development instance may still be running. Closing the window hides Mavo; use **Quit** from its tray/desktop button. Alternatively run `.\scripts\dev.ps1 -Restart` to stop only development instances whose executable belongs to this exact project, then rebuild. This interrupts active work but does not delete saved data. Without `-Restart`, the launcher detects the running instance and explains the conflict before starting Vite or Cargo. It never stops unrelated Mavo installations.

Configure required [Firebase Google login](docs/firebase-setup.md) before opening the workspace. Paste the complete public Web config into ignored `backend/firebase.json`; never use service-account keys. Firebase and connector projects may differ, with a one-time external client allowlist in Firebase. Account identity is stored by Firebase; application content stays local. Isolated domain tests explicitly disable the gate, and session tests verify the production default blocks access before login.

For browser-only UI work, run `npm run dev`. Without Tauri, the interface honestly reports the backend as unavailable. A manually started backend can be connected for development by setting `VITE_MAVO_API_URL` and `VITE_MAVO_API_TOKEN` before starting Vite.

## Checks

`node scripts/check-contrast-ui.mjs` verifies visible text contrast and normal/compact layouts across all eight pages, expanded Google access, Crew creation, route forms, and action settings using temporary WebView debugging. Its action-save check intercepts the request before changing real policy and checks that server-owned `revision` metadata is omitted. `scripts/check-resources-ui.mjs` likewise intercepts consent/logout and checks separate Gmail/Calendar/Tasks capability controls without opening Google or changing the account.

```powershell
.\scripts\check.ps1
```

Or individually:

```powershell
backend\.venv\Scripts\python.exe -m ruff check backend
backend\.venv\Scripts\python.exe -m pytest backend
npm run test
npm run build
cargo check --manifest-path src-tauri\Cargo.toml
npm audit --audit-level=moderate
```

Backend tests use temporary SQLite databases and an HTTP-level Ollama mock. They do not fake production runtime behavior; the mock verifies the provider contract without requiring a developer's local model.

`backend/tests/test_automation_matrix.py` creates disposable Crew through real API endpoints and exercises all three device-report modes with notifications on/off; enabling and revoke/regrant transitions; stop/delete/pause/revocation during a blocked OS read; repeated counters; restart baselines; network/timeout/malformed model output; and fair, bounded scheduler concurrency. It uses controlled fault fixtures rather than touching personal accounts, filling drives, or disabling real networking. Delivery baselines are distinct from physical alert hysteresis: an unauthorized read must not count as a delivered warning. Revocation immediately cancels queued Crew updates, even if no delivery poll occurs before reauthorization.

## Live AIRouter verification

Account providers are not model providers. See [docs/providers/README.md](docs/providers/README.md) and [one-time GitHub App registration](docs/github-app-setup.md). `backend/tests/test_github_auth.py` covers native device authorization, repository selection, token refresh, provider failures, cancellation, account mismatch and Crew use after connection. `backend/tests/test_connectors.py` covers connected-only filtering, exact grants, cached reads, repository limits and verified writes. With temporary WebView debugging enabled, `node scripts/check-github-ui.mjs` mounts the actual connection component in an isolated test root with intercepted HTTP and an injected browser opener: no account authorization or access change is submitted. `node scripts/check-connectors-ui.mjs` checks the actual Resources card/default browser path and optional masked-token fallback without pressing real Connect. Disable debugging afterwards. HTTP fixtures are not evidence of a live connection; real checks require publisher configuration and user consent. No Microsoft registration is needed or supported.

After saving an AIRouter route in the desktop app:

```powershell
backend\.venv\Scripts\python.exe scripts\verify_airouter.py
```

This reads your DPAPI-protected agent key in memory, tests the saved route, and runs all 15 templates with one short prompt each in a temporary workspace database. It checks pause and deletion too. Results go to `.build/airouter-verification.json`. These are real billed provider calls. Existing Crew and conversations are not modified. Use `--list-models` to inspect the catalog or `--model <id>` to test a different model without changing the saved route. Windows DPAPI requires running as your Windows user, rather than a restricted automation account.

Run `backend\.venv\Scripts\python.exe scripts\verify-workers.py` to exercise persistent Run/Stop for all 15 types with the saved AIRouter route. It uses an isolated temporary database, one short call per text type, deterministic Schedule reminders, and explicitly authorized Computer measurements. Results go to `.build/worker-verification.json`. Set `MAVO_WORKERS_ENABLED=false` for deterministic API tests that drive scheduler ticks directly.

Run `backend\.venv\Scripts\python.exe -X utf8 scripts\verify-coordinator.py` with the local publisher gateway running and an existing Firebase login. It exercises live Minimax creation/refinement, idempotency, exact one-time access, and agent-funded important/promotional mail triage in a temporary workspace. Mail snapshots are synthetic; no real inbox is read or email sent. This makes real billed model requests. Run `scripts\verify-core.py` for the same check plus Hyderabad weather.

## Desktop UI verification

`backend\.venv\Scripts\python.exe scripts\verify-automation-examples.py` exercises real C-drive and system counters, recurring delivery/dismissal, daily reminder deduplication, and denied device access in a temporary workspace with zero model calls and no external writes. `node scripts/check-desktop-updates.mjs` requires temporary WebView debugging and the user's already-running C-drive Crew: it explicitly requests a read-only Check now, verifies the actual attached report, tests collapse/unread/reopen, and leaves the message visible. It does not change Crew instructions, permissions, or files.

`scripts/check-status-position.py` now handles unread status-orb labels and tests the larger attached update panel at all four screen edges. Closing a panel preserves an unread event; Dismiss persists acknowledgement first. Automated device regressions cover each-check delivery, warning hysteresis/recovery, revocation, unavailable drives, manual monitor editing, and path rejection. The desktop card has a server-rendering regression test for its safe controls. Runtime timestamps from SQLite without an explicit offset are interpreted as UTC before local display.

`backend\.venv\Scripts\python.exe scripts\check-browser-worker.py --live-model` checks the actual saved AIRouter agent route against a real installed Edge session on example.com. It uses an isolated temporary workspace/profile, allows only open/read/close for that public host, and spends at most three model calls. It verifies actual page data returned by the browser and interpreted by the model, not the user's portal authentication. It never reads Chrome profiles or changes saved Crew. This is opt-in billed provider work.

Regression coverage includes copied action defaults preserving exact limits and explicit Crew denials, named Crew action assignments, isolated browser access, bounded manual sign-in, browser ownership waits without model charges, correction of false missing-tool responses, and repeated-listing notification deduplication. `browser.open` returns metadata; page text requires separate `browser.read` access. No password/CSV/cookie import is implemented.

`backend\.venv\Scripts\python.exe scripts\verify-desktop.py` exercises actual installed Node, a public page in a dedicated Edge session, fixture fill/click controls, an owned Windows test window with verified text input, and one billed AIRouter Core tool-selection call. It uses a temporary database/profile and closes its own windows. It never sends mail or changes your saved action policies. Windows includes Edge; no Chromium download is needed. Browser automation uses Playwright installed in the D-drive virtual environment. `MAVO_AUTOMATION_DIR` selects the profile directory; this workspace configures it on D.

For a temporary development session only, set `$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1'`, launch `scripts/dev.ps1`, and run `node scripts/check-ui.mjs` in a second terminal. The broader legacy script checks screens, builders and real model sessions; it removes its own temporary Crew and preserves user conversations. Screenshots/report go under `.build/ui-check`. It requires a working first route and makes paid model calls. For current multi-chat checks, use `node scripts/check-chats-ui.mjs`: it creates only its own two empty chats, verifies rename/switch/input isolation/confirmed delete, and removes those chats without sending any model requests or modifying existing history. Close that app and remove the environment variable afterward; never ship with debugging enabled.

The UI test checks that Save leaves a Crew stopped, Run uses its saved instructions, Stop persists, editing preserves permission scope, and preview/dismissal work. Overlay tests create and remove their own temporary Schedule Crew and do not depend on the user's reminders. Use `--compact --screens-only` to check the minimum desktop size without model calls. After packaging, `backend\.venv\Scripts\python.exe scripts\check-packaged.py` checks the actual executable with a temporary database.

With temporary WebView debugging enabled, `node scripts/check-desktop-ui.mjs` checks action modes and explanations without saving permissions, and expands/collapses the real desktop button. Close Mavo with the tray or desktop **Quit** command when ending the test; the main window's close button now hides it. Normal launches must have browser debugging disabled.

Run the packaged check again with `--require-login` to verify that the production login gate blocks workspace APIs before Firebase sign-in. The ordinary CRUD smoke uses an explicit test-only login override in its isolated workspace. Google action tests use HTTP fixtures; they never send real email or change your calendar/tasks.

## Database migrations

Startup applies all Alembic migrations. Create a migration from `backend/` after changing ORM models:

```powershell
.\.venv\Scripts\alembic.exe revision --autogenerate -m "describe change"
```

Review generated SQL and add a migration test. Never edit an already released revision.

## Packaging

`npm run tauri build` runs `scripts/build.ps1`, which packages the managed runtime at `src-tauri/binaries/mavo-backend/`, builds the frontend, and then allows Tauri to create the NSIS package. One-directory packaging lets Tauri own the actual backend process directly. Generated binaries and PyInstaller work directories are ignored.

For public Google connection buttons, configure the publisher's Desktop OAuth client once using `MAVO_GOOGLE_CLIENT_ID` or ignored `backend/publisher.json` before packaging. End users never enter client IDs. Missing publisher configuration disables Google connection honestly. See [docs/google-setup.md](docs/google-setup.md) for setup, test users, scope review, and Google's public verification requirements. No real Google connection is claimed by the automated signed-identity/HTTP-contract tests.

Local development also accepts `GOOGLE_CLIENT_ID` and optional `GOOGLE_CLIENT_SECRET` in the project-root `.env` or `backend/.env`; the `MAVO_`-prefixed names are aliases. The desktop secret stays private runtime configuration and is excluded from bundled publisher metadata. Redirects and PKCE are generated by the backend. Restart after editing dotenv configuration.

Google diagnostics: `backend\.venv\Scripts\python.exe scripts\check-google.py` checks the real saved account with minimal probes for Gmail, Calendar, Tasks, Drive metadata and Contacts. It reports missing grants separately from failed API access and never prints credentials or user content. `--require-all` requires all five services after browser consent. Drive/Contacts probes count toward their persistent 200-request per-device daily limits.

## Logs and local data

The backend log and database live under the Windows local application-data directory for `dev.mavo.desktop`. Logs must never contain tokens, prompts, credentials, or message bodies.

## Publisher-funded normal chat

For Google connection failures in a packaged sidecar, verify the publisher's `/health` has `google_oauth_configured: true`, not just the Core AI key configured. Root development Google aliases or publisher-prefixed server credentials are supported; none are included in installer metadata. See [Google setup](docs/google-setup.md#packaged-sign-in-configure-the-existing-publisher-service). With a temporary native WebView debug session, `node scripts/check-google-flow-ui.mjs --check-services` prints only safe configuration/grant/probe outcomes. `--open` starts browser consent but never approves it. Always disable temporary WebView debugging afterward. Broker tests are in `backend/tests/test_publisher_oauth.py`; the existing Google suite also tests native→publisher→Google exchange/refresh using signed fixture identities.

Follow [publisher/README.md](publisher/README.md) to start the local gateway in a separate terminal with `.\scripts\publisher.ps1`. Publisher settings live in ignored `publisher/.env`; the shared key is encrypted with DPAPI on Windows. The desktop uses `MAVO_CORE_GATEWAY_URL` in root `.env`, never a shared key. Firebase sign-in must be from the same project as the gateway. Agent connection keys saved through Settings remain separate. The normal-chat UI identifies this service only as MAVO CORE. Settings provides Logout.

Resource/location regressions are in `backend/tests/test_resource_access.py` and `src/lib/geolocation.test.ts`. Revocation and simple GitHub auto-connect cases are in `backend/tests/test_connection_revocation.py`. With temporary local WebView debugging, `node scripts/check-resources-ui.mjs` checks automatic location, a single Connect Google requesting supported scopes, visible access disclosures and Logout; it intercepts sensitive requests before I/O. `node scripts/check-connectors-ui.mjs` checks compact account cards with no setup/token forms and connected-only Actions. `node scripts/check-github-ui.mjs` simulates authorization, automatic installation handoff/completion, Connected and Revoke using only isolated fixtures. `scripts/revoke-connections.mjs --revoke` is a real destructive credential/access reset: use only when explicitly requested, never as a routine UI test. Its `--confirm-github` flag records a user's provider-side revocation confirmation, not an API-verified receipt. Disable debugging after verification.

Natural-language Chat and Create Crew use `POST /api/v1/core/turn`; legacy `/chat`, `/core/chat`, and per-action `/approve` return 410. Automatic creation is safe before access is granted. Saving requested access starts ready Crew; actions execute under saved rules. Existing manual Crew editors still save stopped Crew until Run. Migration 0012 is additive and converts old ask-every-time action rules to disabled; automatic scoped rules retain their limits. Back up SQLite using its backup API before upgrading.

`npm run tauri build` invokes release validation: set the deployed public HTTPS gateway URL and publisher Firebase/Google identity first. Packaging the development sidecar directly with `.\scripts\package-backend.ps1` permits local URLs for testing. The Docker HTTPS deployment recipe is supplied, but deployment requires your server/domain and is not yet live.

`node scripts/check-coordinator-ui.mjs` verifies the live description-first builder, refinement without duplicates, loading state, mail event configuration, missing-access boundary, one main chat, and absence of logout/model pickers. It uses an independent planning session and removes only its own temporary Crew; no inbox permission is granted. Keep the local publisher gateway running. Requests use real publisher credits. Provider timeouts fail explicitly and preserve the saved configuration; no paid requests are silently repeated.
