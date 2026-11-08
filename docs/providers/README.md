# Adding account providers and actions to Mavo

This is the maintainer guide for **resource/account connectors**, not model API routes. OpenAI, Anthropic, Ollama, OpenRouter and AIRouter model routes remain separate in Settings.

## What works now

- Local: device counters, weather/location permissions, bounded Terminal, separate Browser and owned Windows tools. These do not require Google Workspace.
- Google Workspace: Gmail headers/optional contents, primary Calendar, default Tasks; existing send/archive/mark-read/trash/calendar/task actions plus the new **create email draft** action. Draft creation never calls the send endpoint. Firebase app login does not connect these services.
- Google read extensions: Drive file metadata and saved Contacts via People API, with independent read grants and cached bounded collection. No file contents or contact writes. [Scopes and cost limits](../plugins/SETUP.md#drive-and-contacts--read-only-plugins).
- Public feeds: independently enabled arXiv AI and BBC Technology titles/dates/links. Keyless, fixed endpoints, per-Crew access and safe bounded parsing. Not an arbitrary RSS URL fetcher.
- GitHub: browser-based GitHub App device authorization, selected repository metadata/issues, open PRs and CI status, plus issue/comment actions. Each capability is separate. Selected repositories remain a hard limit even with “Allow without limits.” Users do not need keys; an advanced fine-grained-token fallback remains available. The publisher registers one app for all users. Webhooks, workflow execution and code modification remain unsupported. [Publisher setup](../github-app-setup.md), [user setup](../github-setup.md).

Disconnected account actions are omitted from Actions and agent execution schemas. Read-only consent does not expose write actions. Resources still offers connection choices. Setup may propose missing access, but cannot activate a Crew until the connection, exact Crew grants, and saved action rules permit it.

Google Drive metadata/Contacts and curated public feeds are separate opt-in resources. Slack and IMAP/SMTP are not implemented. Add new adapters only for a concrete responsibility through the process below. A provider name or JSON manifest alone never installs executable integrations.

## Quick setup for the supported resources

No Azure subscription, Entra directory, or Microsoft app registration is needed. Microsoft account connections and tools have been removed.

### 1. App sign-in (publisher setup once)

Keep your existing working Google OAuth credentials; do not recreate them. Fill `backend/firebase.json` with the **public Web app configuration** from Firebase Project settings. Enable Google under Authentication → Sign-in method. If the Desktop OAuth client belongs to a different Cloud project, allowlist its public client ID in Firebase's Google provider. Never paste an Admin SDK/service-account private key.

Public users just click **Sign in**. Signing in identifies their Mavo workspace; it does not grant mail/calendar/task access. Current data stays local; Firebase login is not cloud data synchronization. Full guide: [Firebase setup](../firebase-setup.md).

### 2. Google Workspace (publisher setup once, user connects when needed)

1. In your existing [Google Cloud project](https://console.cloud.google.com/), enable **Gmail API**, **Google Calendar API**, and **Google Tasks API** under APIs & Services → Library.
2. Configure Google Auth Platform branding, External audience, and required data-access scopes. While Testing, add the exact Google account you will use under **Test users**.
3. Create or keep the OAuth client of type **Desktop app**. Do not use a Firebase Web client or service account for Workspace.
4. In the repository-root `.env`, keep:

   ```dotenv
   GOOGLE_CLIENT_ID=your-desktop-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-desktop-client-secret-if-required
   ```

5. Restart Mavo. Open **Resources → Google Workspace**. Use **Connect all** for the implemented Gmail/Calendar/Tasks access, or the separate **Access** controls for only the services/permissions required. Returned consent, not selected checkboxes, determines what works.
6. Give each Crew only its requested read/write grants. Enable its specific write actions in **Actions**. Neither a Google connection nor app login automatically authorizes a Crew to send mail or edit events.

Keep secrets in ignored local configuration, not chat or source control. End users never copy client IDs or secrets to you. Packaging includes only public application identity. Full scopes, troubleshooting, test-user flow and release verification: [Google setup](../google-setup.md), [official credential setup](https://developers.google.com/workspace/guides/create-credentials).

Current mail tools: create draft, send/reply, archive, mark read and move to trash. Calendar tools: create/update/delete primary-calendar events. Task tools: create/update/complete/delete tasks. Existing mutations require current versions. Reads are bounded; this is not complete mailbox/calendar synchronization. Drive **metadata** and Contacts **read-only** adapters are implemented; document content, Docs/Sheets tools and contact writes are not. See [provider keys and setup](../plugins/SETUP.md) for scope, privacy and quota limits.

Before public release, complete the applicable Google OAuth/scope verification. Gmail scopes include restricted access; sending that information to cloud models can add review/security-assessment requirements. Do not treat a successful personal test as public verification. See [Google restricted-scope readiness](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification).

### 3. Weather and location (no key for current non-commercial testing)

In **Resources → Default resources**, enable **Weather** for destination weather queries and **Location** only if you want computer-location detection. For automatic detection, allow location access in Windows **Settings → Privacy & security → Location**; on managed PCs this may be controlled by an administrator. Mavo cannot bypass a denied or unavailable Windows location service and does not pretend detection succeeded.

Weather access is not restricted to the computer's position: a permitted Crew can request Chennai or another explicitly identified destination without Location permission. Same-named places must be disambiguated by city, region and country, not silently guessed.

The current adapter uses Open-Meteo with caching; there is no weather API key field. Its free hosted API is for non-commercial use, subject to limits and without an uptime guarantee. Before commercial distribution, arrange an appropriate commercial provider/endpoint or a compliant self-hosted service; the customer-key endpoint is not yet implemented in this adapter. [Open-Meteo pricing and licensing](https://open-meteo.com/en/pricing).

### 4. Local device, browser and Windows tools (no cloud account)

- **Computer Crew:** approve device metrics, choose system/storage monitoring, interval and desktop notifications. No Google account connection is needed.
- **Browser:** enable only required operations in Actions and restrict sites where possible. Mavo uses its separate persistent browser profile for each Crew. Open that browser to sign in manually; do not import passwords, cookies or your personal Chrome profile.
- **Terminal:** the supported diagnostic/Git command catalog only, within saved folder limits; not arbitrary shell execution.
- **Windows:** only supported app/window controls under saved action rules; browser/terminal input remains separately guarded.

A Crew still needs a model route for model-driven work. Device checks and reminders can run deterministically without spending model calls. Define named model routes in **Settings** with your chosen provider, key and model; these are separate from resource connections and MAVO CORE chat.

Tasks run only while Mavo is open and Windows is awake. None of these connectors provides hosted always-on execution.

## User setup: GitHub

Use the full [GitHub connection README](../github-setup.md) for PR/CI permissions, sample Crew and troubleshooting.

1. Open GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Give it a name and expiry. Choose the resource owner and **Only select repositories**, then select the repositories Mavo should use. An organization may require administrator approval.
3. Keep **Metadata: Read-only**. Add **Issues: Read-only** (or **Read and write** for issue writes), **Pull requests: Read-only** for PR monitoring, and **Actions: Read-only** for workflow/CI status only as needed. Do not grant administration, Actions write or source-code write permissions for these tools.
4. In Mavo, open **Resources → GitHub → Connect**. Paste the token into the masked field, enter the exact `owner/repository` names (one per line), and choose matching capabilities. Save the connection. Do not put the token in `.env`, chat, or the README.
5. Create/refine a Crew, grant its needed repository/issue access, and set an interval. For writes, enable only **Create issue** or **Comment on issue** in Actions with exact repository limits. Read-only monitoring does not need either write action enabled.

Mavo validates account identity and access to each selected repository before saving the encrypted token. It cannot preflight an issue write without writing; GitHub remains authoritative at execution. A rejected permission is removed from available actions until reconnection. No source is executed or modified. For public one-click GitHub integration, implement the planned GitHub App flow rather than distributing a shared token. [Fine-grained token guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens), [issues API](https://docs.github.com/en/rest/issues/issues).

## Source map

- `backend/src/mavo/connectors/catalog.py`: server-owned provider capabilities, action metadata, and exact Crew grant names.
- `connectors/hub.py`: GitHub account metadata, credential references, connection checks, normalized bounded reads, shared cache, and provider-scoped disconnection.
- `connectors/actions.py`: strict Pydantic argument models and bounded provider execution/read-back verification.
- `connectors/api.py`: authenticated account connection/read endpoints.
- `google_auth.py`, `google_permissions.py`, `resources/google.py`: existing Google OAuth and resource adapters; kept compatible rather than replaced.
- `google_actions.py`: legacy-named shared permission/audit executor. Google and GitHub run through its existing claims, generation/revision fences, no-replay behavior, and audit records.
- `action_rules.py`: user-owned access modes and exact recipient/repository/site/folder limits.
- `schemas.py`, `crew_planner.py`, `crew_workers.py`: proposed connector grants, explicit authorization, snapshots, and model tool schemas.
- `src/components/ConnectorAccounts.tsx`: optional account UI; `CrewAccountPermissions.tsx` and `CrewSetupCard.tsx`: exact per-Crew choices.

## Add another provider

1. Define concrete read responsibilities first. Select normalized fields, pagination limits, cache TTL, event semantics, required OAuth scopes, and which data may reach the selected cloud model. Never start with “access everything.”
2. Add stable namespaced capability IDs to `connectors/catalog.py`, e.g. `example:tasks:read`. Keep read, body/content, send, write, and destructive access separate. `validate_capabilities` must reject unknown names.
3. Implement fixed-authority HTTP requests and credential handling in a dedicated connector module. Register the provider in the hub's status/subject/read/token dispatch and `connectors/api.py`. Do not accept arbitrary base URLs, token endpoints, redirect destinations, or provider-supplied pagination URLs without validation.
4. Use the existing credential store for secrets. Persist only account identity, allowed scopes, selected repositories/resources, and freshness metadata in `WorkspaceSetting` (current connectors use `connector-<provider>` keys). New schema tables require additive Alembic migrations, not runtime destructive changes.
5. For OAuth, use a public desktop client with PKCE, state, bounded callback ownership, cancellation, account pinning, refresh rotation, and explicit returned-scope handling. Verify identity using validated OIDC claims or a fixed authenticated provider identity endpoint. Never parse an unverified ID token as proof of identity. Configure application identity once as the publisher, not once per end user. If a provider requires a confidential client, implement a publisher-side OAuth broker; do not hide its shared secret in the desktop.
6. Add provider-specific setup to `config.py` and `publisher.py`; package only public application identity. Add only the exact official authorization host/path to Tauri's `open_google_url` legacy-named command. Never make it a general URL/shell launcher.
7. Register normalized bounded reads in the hub. Workers call `connector_snapshots`, which checks exact Crew grants before and after I/O and labels contents untrusted. Do not import an adapter directly into a model prompt or expose credentials.
8. Update coordinator capability guidance and connected context. Prefer already-connected providers; ask which account a user wants if none is connected. A request is a proposal, not authorization. Add availability to Resources without fabricating a connection.
9. Add disconnect/revocation handling scoped only to this provider: clear tokens/cache, fence and stop affected work, remove its Crew grants, and redact its derived outputs/action payloads. Leave other providers and ordinary user-entered chat intact. A 401/403 should remove unavailable capabilities and stop affected work, not silently broaden scope or switch providers.
10. Add contract/fault tests before enabling the provider. Document setup, real coverage, limitations, and public-release requirements here.

The current registry is intentionally server-owned and statically registered. A user cannot install arbitrary executable adapters by asking a model or pasting a manifest. Future dynamic SDK packaging needs a separate trust/isolation design.

## Add an action

For a new external action such as `example.create_task`:

1. Add a catalog entry in `connectors/catalog.py`:

   ```python
   ACTIONS["example.create_task"] = (
       "example", "tasks:write", "Example Tasks", "Create task", False
   )
   ```

2. Define a strict argument model in the provider actions module and register it in `connectors/actions.py`'s `MODELS`. Use `extra="forbid"`, bounded strings/arrays, exact IDs, single-line headers, timezone-aware dates, and version/ETag checks for existing items. Never accept arbitrary code or raw HTTP requests.
3. Add the operation to the `Operation` allowlist in `google_actions.py`. The shared action catalog imports provider metadata automatically, and `action_grant` resolves `example:tasks:write`. Do not misclassify an external provider as a local service to bypass account checks.
4. Implement execution behind a guard check before and after each request. `perform` must use the fixed provider client, verify the connected account, enforce selected-resource limits even in unrestricted mode, and check exact Crew grant plus saved action rule. For new custom limits, extend `Rule`, `RuleChange`, `check`, `SuggestedAction`, the frontend `ActionRule`, `ActionAccessEditor`, and `actionRuleInput`. Server-owned revisions must never be echoed as editable fields.
5. Use idempotency keys where supported. Claim work durably before I/O. Definitive 4xx rejections are failures; uncertain network/5xx outcomes remain unknown and are not automatically replayed. Do not retry sends or writes just because the UI timed out.
6. Verify outcomes accurately: read back a created/edited object and relevant fields, or report only provider acknowledgement if stronger evidence is unavailable. A 202 send acknowledgement is not recipient delivery. Do not claim a draft was sent.
7. Add capability labels in `ConnectorAccounts.tsx`. Actions groups are generated from connected catalog entries; no empty/disconnected provider groups should appear. Crew setup explicitly reviews proposed access after its questionnaire.
8. Test allowed/denied modes, scoped recipients/repositories, malformed arguments, account switching, stale revisions, concurrent claims, revoke/delete/sign-out during I/O, 401/403/429/5xx, uncertain outcomes, no replay, and read-back mismatches. Never use real mail/calendar writes in ordinary tests.

## Verification commands

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_connectors.py -q
backend\.venv\Scripts\python.exe -m pytest backend -q
npm run check
.\scripts\check.ps1
```

Tests use temporary databases, encrypted-store-compatible in-memory credentials, and HTTP contract fixtures. They do not prove live Google consent or a GitHub token's current permissions. Verify those manually after publisher registration or account connection, with an explicit safe test action and user-selected target. Never commit client secrets, tokens, `.env`, databases, screenshots containing private data, or runtime credentials.
