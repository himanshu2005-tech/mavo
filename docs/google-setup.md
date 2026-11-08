# Google connections: publisher setup and public users

Public users click **Resources → Google → Connect Google**. This requests supported Gmail header/content/send/modify, Calendar read/write, Tasks read/write, Drive metadata read and Contacts read capabilities in one consent flow. Google lets users approve or decline permissions; the card shows Connected and lists the actual grants. There are no service setup matrices in the normal UI. Users never create projects or copy client IDs, secrets, passwords or tokens. [Simple guide and Revoke access](connecting-accounts.md).

The publisher configures one Google OAuth application before distributing Mavo. Required [Firebase login](firebase-setup.md) identifies the workspace owner; it does not authorize Gmail, Calendar, or Tasks. Native browser consent, PKCE, the loopback callback and signed identity checks stay on the desktop. Packaged builds exchange/refresh tokens through the existing publisher service so the required OAuth client credential is not shipped to users. A source development launch may use its private local credential directly.

The compact Google card describes requested access before connection and actual capabilities afterwards. Per-Crew grants remain independent in Crew setup. **Revoke access** removes local credentials, snapshots and affected Crew grants, then requests provider revocation. A failure still disconnects locally and clearly links to Google for the remaining remote step; success is not fabricated. Drive/People APIs must be enabled. Docs/Sheets contents and contact writes remain unsupported. [Coverage and cost limits](plugins/SETUP.md#drive-and-contacts--read-only-plugins).

## 1. Create the publisher project once

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create a project for Mavo under an account or organization you control.
2. In **APIs & Services → Library**, enable **Gmail API**, **Google Calendar API**, **Google Tasks API**, **Google Drive API**, and **People API**. Keep Cloud Billing disabled for strict no-charge testing; Mavo does not enable billing or buy quota.
3. Open **Google Auth Platform → Branding**. Enter Mavo's application name, support address, developer contact, home page, and privacy policy. Configure and verify your publisher domain for public distribution.
4. Under **Audience**, choose **External**, initially keep **Testing**, and add your own Google account as a test user. This stage is for development, not unrestricted public access.
5. Under **Data Access**, declare `openid`, `email`, `profile`, and the supported capabilities from the scope catalog below. Connect all requests all ten supported capabilities; per-Crew grants and action settings remain separate.
6. Under **Clients**, create an OAuth client with application type **Desktop app**. Name it Mavo Windows. Do not substitute a web/Firebase client or service account.

The callback is a random-port `http://127.0.0.1:<port>/oauth/callback` listener owned by Mavo. Authorization uses PKCE, state, and a nonce; ID tokens are signature, audience, issuer, expiry, and nonce checked. Sign-in is in the default browser, not an embedded Google login. See [Google's desktop OAuth guide](https://developers.google.com/identity/protocols/oauth2/native-app).

Identity timestamp validation allows a bounded 60-second clock skew. This prevents valid freshly issued Google tokens from failing on small issuer/device clock differences. Signature, issuer, audience, required claims, and nonce checks remain enforced; large future timestamps and tokens expired beyond the allowance are rejected.

## 2. Configure the public identity in the release

The desktop client ID identifies Mavo and is public. End users' refresh tokens remain private and DPAPI encrypted on their own Windows account. Never put a web application secret, service-account private key, or user token in the desktop bundle.

For development, set the publisher ID before launching:

```powershell
$env:MAVO_GOOGLE_CLIENT_ID = 'YOUR_DESKTOP_CLIENT_ID.apps.googleusercontent.com'
.\scripts\dev.ps1
```

You can instead put the Desktop OAuth credentials in the project-root `.env` (or `backend/.env`):

```dotenv
GOOGLE_CLIENT_ID=YOUR_FULL_DESKTOP_CLIENT_ID
GOOGLE_CLIENT_SECRET=YOUR_DESKTOP_CLIENT_SECRET_IF_REQUIRED
```

`MAVO_GOOGLE_CLIENT_ID` and `MAVO_GOOGLE_CLIENT_SECRET` are also supported. Actual environment variables override dotenv files. Source launches find the project-root dotenv even when the backend starts from `backend/`; backend-local values override project-root values. Restart Mavo after changing credentials. The secret is private runtime configuration, is excluded from settings dumps, and is not included in the release publisher file. Keep `.env` out of source control; the Vite server denies access to it. Redirect URL, scopes, state, and PKCE are managed by Mavo and need no additional dotenv entries.

For a packaged release, set the same variable before building:

```powershell
$env:MAVO_GOOGLE_CLIENT_ID = 'YOUR_DESKTOP_CLIENT_ID.apps.googleusercontent.com'
npm run tauri build
```

Alternatively copy `backend/publisher.example.json` to ignored `backend/publisher.json` and fill `google_client_id`. The packaging script also reads the dotenv aliases and writes only the public client ID into the sidecar's bundled `publisher.json`; it does not copy the secret or arbitrary publisher configuration. Environment/dotenv configuration takes precedence over the JSON file. Users do not need that variable after installing. A missing ID leaves connection buttons disabled with an honest unavailable message.

Developer-only per-workspace client editing requires the explicit `MAVO_ALLOW_GOOGLE_DEVELOPER_SETUP=true` environment flag. It is disabled by default and cannot override publisher identity. Desktop clients can still require their generated credential at Google's token endpoint; do not assume that PKCE makes omitting it work for every client. On this project, Google rejected a request without it as `invalid_request` / missing `client_secret`. Mavo now reports this as publisher setup, not an account failure.

### Packaged sign-in: configure the existing publisher service

In the publisher service's private environment (not the desktop bundle), set:

```dotenv
MAVO_PUBLISHER_GOOGLE_CLIENT_ID=your-desktop-client-id.apps.googleusercontent.com
MAVO_PUBLISHER_GOOGLE_CLIENT_SECRET=your-desktop-client-credential
```

For local development, the service also reads the existing root `.env` aliases `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`; no duplicate values are needed. Explicit publisher-prefixed values take precedence. Restart `.\scripts\publisher.ps1` after changes. Its `/health` response should show `google_oauth_configured: true`. Keep `MAVO_CORE_GATEWAY_URL` pointed at this service—loopback only for local testing, HTTPS for public users. Rebuild/restart Mavo after backend changes; an older packaged sidecar cannot use the new endpoint.

The desktop POSTs only its one-use authorization code, PKCE verifier, public client ID and exact loopback redirect to `/v1/google/token`. Refresh uses the private refresh token. The server accepts only its allowlisted client IDs and supported scopes and supplies its own credential to Google's fixed token endpoint. It does not store user tokens or request bodies; responses are non-cacheable, validation errors never echo credentials, and attempts have separate durable rate/concurrency limits. The desktop still validates signed identity/state/nonce and saves its tokens with DPAPI.

This bootstrap exchange cannot require an already-existing Firebase login; successful Google code/PKCE or a valid refresh token is required instead. It does not grant Workspace access by itself and cannot execute mail/calendar/task tools. A publisher HTTPS service is an external release prerequisite, and its transient handling of tokens belongs in the privacy/data-flow disclosure. See [publisher deployment](../publisher/README.md).

If using a separate Firebase Desktop client, configure that exact ID/credential on the publisher too using `MAVO_PUBLISHER_FIREBASE_GOOGLE_CLIENT_ID` and `MAVO_PUBLISHER_FIREBASE_GOOGLE_CLIENT_SECRET`. Existing source aliases remain supported. Do not change your working Workspace client to fix an unrelated Firebase project allowlist.

## 3. Verify the real account flow

1. Launch Mavo, complete Firebase login, and click **Resources → Google Workspace → Connect all**. Select your test account and review the supported permissions in Google's consent screen.
2. Return to Resources. The connection updates after browser consent, reflecting actual grants rather than claiming declined capabilities. Missing access may require connecting again. Ensure all five APIs are enabled in the publisher project.
3. Create a Communication Crew, choose **New email**, explicitly grant Gmail headers, and optionally set sender/subject filters. Save, then **Run**. It establishes a baseline without treating existing inbox mail as new arrivals. A later matching inbox arrival creates a bounded work session; a refresh with no matching domain event consumes no model calls.
4. Restart Mavo and verify token refresh and Crew resume. Disconnect Google and verify synced snapshots, local tokens, and Google-derived worker results are removed and affected workers stop. The remote app grant can also be removed in [Google account connections](https://myaccount.google.com/connections).

Adding a service or capability reopens consent for the union of existing and requested scopes. Native installed apps do not use Google's web-only incremental authorization feature. Users may decline individual scopes; connection state reflects the actual returned grant. Each Crew still needs its own explicit resource and action grants. Connecting write access never authorizes an action by itself: configure its access settings in Actions; execution receipts appear in Activity or Resources.

Use **Cancel sign-in** to stop a pending attempt without disconnecting an existing account. Adding services hints the already-connected account; choosing a different account produces an explicit account-mismatch error and preserves the existing connection. Successful extensions retain the same account's sync cursors. Partial consent lists missing services rather than claiming all services are connected. Failed token exchange, expired links, identity verification failures, and credential-storage errors have distinct safe diagnostics; provider response bodies and credentials are never displayed or logged.

The diagnostic connection checker makes minimal authenticated API probes and distinguishes verified access, missing authorization, and service errors such as a disabled Calendar API. It does not fetch message bodies, calendar event details, or task contents. Run it from PowerShell:

```powershell
backend\.venv\Scripts\python.exe scripts\check-google.py
```

The report is stored in ignored `.build/google-connection-check.json` without account identifiers or content. Add `--require-all` for a nonzero exit when any of the five services is not verified. Automated tests cover complete/partial consent, scope upgrades, account mismatch, invalid/expired identity, safe token errors, cancelled/denied consent, concurrent attempts, cancellation during exchange, preserved credentials/cursors, missing scopes, disabled APIs, cached authorization and persistent daily read limits. Real access still requires user consent and enabled APIs in the publisher project.

## 4. Complete public verification before distributing Google access

Move beyond test users through Google's production and verification process. Prepare accurate branding, an owned/verified domain, a public privacy policy, scope justifications, and a demonstration of each requested scope. Publishing the consent screen does not by itself complete verification.

Gmail metadata is a **restricted** scope even though it excludes bodies and attachments. Public apps generally require restricted-scope verification unless an exception applies. Sending restricted data to a server, including a cloud model provider, can add security-assessment requirements. Do not assume that a desktop/local database automatically exempts every Mavo configuration. Review [Gmail scope classifications](https://developers.google.com/workspace/gmail/api/auth/scopes) and [Google's restricted-scope verification requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification) against the final data flow. Crew configuration explicitly warns that authorized event data may be sent to its selected model route.

Actual publisher credentials and Google approval are external release prerequisites. Automated tests use signed test identities and HTTP fixtures; they do not mean a real Google application is verified or connected.

## Current sync coverage

- Gmail: recent inbox headers and subsequent inbox message-added history. Plain-text bodies require separate content permission. Policy-controlled actions support creating drafts (without sending), sending/replies to one recipient, archive, mark read, and trash. Attachments, permanent deletion, and full mailbox synchronization are not implemented.
- Calendar: primary-calendar events in the next 30 days, bounded to 500 items. New or updated entries produce events; deletion/completion and every calendar are not fully synchronized.
- Tasks: incomplete tasks in the default list, bounded to 500 items. New or updated entries produce events. Policy-controlled actions support creating, updating, completing, and deleting tasks; Calendar similarly supports creating, updating, and deleting primary-calendar events. Existing mutations require an exact current entity version. Complete task/account synchronization is not implemented.
- Drive: up to 100 recent file metadata records, five-minute cache. No contents, downloads, writes or selected-file isolation. Contacts: up to 100 saved names/emails/phones, 15-minute cache; no directory/other contacts or writes. Crew sessions receive up to ten items per Google resource. Both use explicit read grants, scheduled reviews (no change triggers yet), and a persistent 200-request per-service/device/day ceiling. This is not a project-wide spending limit. [Current costs/quotas](plugins/SETUP.md#cost-and-public-release-limits).
- Active resource-triggered Crew share a 60-second resource cache. Resource events and rule cursors persist across restarts. Deterministic sender/subject matching happens before inference. Daily reminders remain a separate trigger type.
- Work runs while Mavo and Windows are awake. This release has no hosted Gmail push listener and cannot process events while the computer is off. Expired Gmail history requires reconnection and a fresh baseline.

These limitations should remain visible in the product and release notes. Other account providers need their own adapters and publisher OAuth registrations; one Google connection cannot authorize them.

## Capability scope catalog

All suffixes below use the prefix `https://www.googleapis.com/auth/`:

- `gmail:read`: `gmail.metadata`
- `gmail:body`: `gmail.readonly`
- `gmail:send`: `gmail.send`
- `gmail:modify`: `gmail.modify`
- `calendar:read`: `calendar.readonly`
- `calendar:write`: `calendar.events`
- `tasks:read`: `tasks.readonly`
- `tasks:write`: `tasks`
- `drive:read`: `drive.metadata.readonly` (restricted)
- `contacts:read`: `contacts.readonly`

Enable the matching APIs in the publisher project. Users click Connect Google once and review Google's actual consent screen; Crew still require independent grants. Gmail content/modification scopes and Drive metadata require applicable restricted-scope review before public distribution. Typed per-capability API endpoints remain available for Crew setup and developer clients, not as extra Resources forms.

For safe testing, leave actions Off until you have checked their scopes, then enable only the operations you intend to run. Automated write tests use HTTP fixtures and never send real mail. Ambiguous outcomes are marked unknown and are never automatically replayed; check Google before issuing another operation.
