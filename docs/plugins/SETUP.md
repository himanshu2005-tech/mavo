# Provider keys and setup: all requested Mavo integrations

Do not paste keys into chat. Publisher credentials belong on the publisher service; each user's provider token belongs in Mavo's secure connection form. Tokens entered in working connection forms are protected by Windows DPAPI. Model API routes in Settings are separate from resource integrations.

## Implementation status

**Working:** Google Gmail/Calendar/Tasks, read-only Drive file metadata, read-only Contacts (People API), GitHub repository/issue/open-PR/workflow-status resources, issue/comment actions, curated public RSS/Atom feeds, weather/location and local tools.

**Preparation only—not implemented:** Google document contents/Docs/Sheets editing, Contacts editing, Notion, Telegram and arbitrary user-entered feed URLs. There are no working key fields or hidden `.env` switches for these future adapters. Enabling an API or creating a credential does not install an integration.

## Google Workspace: Gmail, Calendar, Tasks, Drive and Contacts — working

### Publisher: one setup for the application

Keep your existing Google **Desktop app** OAuth client. Enable Gmail API, Google Calendar API, Google Tasks API, **Google Drive API** and **People API** in its Cloud project. Configure External/Testing and add your test account until production verification is complete. Declare the scopes in [Google setup](../google-setup.md#capability-scope-catalog). No new client ID, Firebase project or end-user API key is needed for Drive/Contacts.

For the local publisher service, your existing project-root `.env` is supported:

```dotenv
GOOGLE_CLIENT_ID=your-desktop-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-desktop-client-credential
MAVO_CORE_GATEWAY_URL=http://127.0.0.1:8741
```

For a hosted service, configure the equivalent **server-only** variables:

```dotenv
MAVO_PUBLISHER_GOOGLE_CLIENT_ID=your-desktop-client-id.apps.googleusercontent.com
MAVO_PUBLISHER_GOOGLE_CLIENT_SECRET=your-desktop-client-credential
```

Set the desktop publisher build's `MAVO_CORE_GATEWAY_URL` to that service's public HTTPS URL. Do not ship any `.env`, OAuth secret, service-account key or shared AIRouter key in an installer. `publisher/.env.example` documents the server settings; empty example fields do not override valid root aliases. Restart the publisher after changes and rebuild/restart Mavo after backend updates.

For Firebase login, paste the full **public Web app configuration** into `backend/firebase.json` and enable Google authentication. If it uses a different Cloud project, allowlist the existing Desktop client's public ID in Firebase's Google provider. Firebase identity is not Workspace consent.

### User: no keys or client IDs

Open **Resources → Google → Connect Google**. Read the access summary, review Google's consent screen and return; Connected updates automatically. No service forms or Save step. Give each Crew only the needed resource permissions and separately enable write Actions. Read-only consent never permits writes. [Simple connection/revocation guide](../connecting-accounts.md).

Read [Google setup](../google-setup.md) and [Firebase setup](../firebase-setup.md) for exact scopes, errors and release requirements. The missing-client-secret error is fixed through publisher-side token exchange; users should not create another OAuth project to work around it.

## GitHub — working

**Normal users:** click **Resources → GitHub → Connect GitHub**, enter the short code on GitHub and authorize. If installation is requested, choose repositories on GitHub; Mavo detects completion and connects automatically. No key, permission matrix, Refresh list or Save step. More than ten shared repositories require a bounded selection instead of silently choosing a subset. **Publisher once:** [register the GitHub App](../github-app-setup.md) and bundle its public client ID/slug. No publisher secret/private key is required for device authorization/refresh.

**Optional advanced token fallback:**

Create a user-owned fine-grained token with selected repositories and an expiry. Add only needed repository permissions:

- Metadata: **Read-only**.
- Issues: **Read-only**, or **Read and write** for issue creation/comments.
- Pull requests: **Read-only** for PR monitoring.
- Actions: **Read-only** for workflow/CI status.

The fallback is now developer-API only: authenticated `POST /connectors/github/connect` accepts `token`, exact `repositories` and supported `connector_capabilities`. No token form is shown in Resources, and no end-user token belongs in `.env`. Existing token connections remain supported. PR/CI grants never permit merges or workflow execution.

Full instructions, examples, troubleshooting and current public-user limitations: [GitHub connection README](../github-setup.md).

## Drive and Contacts — read-only plugins

1. Publisher: enable **Google Drive API** and **People API** in the existing OAuth project. Add `https://www.googleapis.com/auth/drive.metadata.readonly` and `https://www.googleapis.com/auth/contacts.readonly` under Google Auth Platform → Data Access. Keep Cloud Billing disabled for strict no-charge testing. Restart the publisher gateway and rebuild/restart the desktop backend after a code update.
2. User: click **Resources → Google → Connect Google**, review the requested services in Google's consent screen and approve the access you want. Drive/Contacts are included as read-only resources. The card lists actual grants; failed/cancelled upgrades preserve an existing connection.
3. Crew: explain the responsibility in Chat or Add Crew. After answering the setup questions, grant **Drive metadata** or **Contacts**, select an agent route and verify the setup. The coordinator can suggest these known capabilities but cannot grant them. Save and Run; use scheduled reviews for these resources, not unsupported Drive/Contacts change triggers.

Drive returns up to **100 recently modified files** with IDs, names, MIME types, modification dates and links, cached for five minutes. It does not read/download document contents or export Docs/Sheets, and does not upload, edit, share or delete files. This scope permits metadata across the account; the current Crew grant is service-level, **not selected-file isolation**. Do not grant it to a Crew that should see only one confidential folder.

Contacts uses the **People API**, returning up to **100 saved contacts**, names and up to three email addresses/phone numbers each, cached for 15 minutes. It excludes directory and “other contacts,” biographies, photos and writes. It is not a complete address-book sync. A Crew gets at most ten entries per Google resource in a bounded work session; it must not claim to have searched the whole account.

Only explicitly permitted data enters a Crew's selected model route. Local snapshots are stored in SQLite (not encrypted by default); OAuth credentials are DPAPI-protected. Disconnect clears Google-derived snapshots/results and stops affected work. Unchecking an already-issued Google scope is not revocation: edit Crew access or disconnect/revoke the app in your Google account.

### Cost and public-release limits

No extra paid connector, service account or API key is required. Do **not** assume unlimited free usage: Google's current [Drive limits](https://developers.google.com/workspace/drive/api/guides/limits) describe no-additional-cost standard usage and planned 2026 overage billing. Keep Cloud Billing disabled during strict no-charge tests; Mavo never enables billing, buys quota or retries a quota failure automatically. Each desktop persistently caps Drive and Contacts at **200 HTTP requests per service per UTC day**, including connection checks; cache hits consume none. This is a per-device safety ceiling, **not a shared-project spending cap**. People API also enforces [contact-list quotas](https://developers.google.com/people/api/rest/v1/people.connections/list), including initial full-sync limits. Model credits, storage, hosting and public OAuth verification/security assessments are separate costs.

`drive.metadata.readonly` is a **restricted** Google scope. Complete applicable [scope verification](https://developers.google.com/workspace/drive/api/guides/api-specific-auth) and assessment requirements before releasing to the public, especially when sending metadata to cloud models. Selected-file content access and Docs/Sheets tools remain future work; enabling those APIs alone adds no Mavo tools.

## Notion — future adapter

For an initial personal/internal test:

1. Follow the [Notion developer portal link in its authorization guide](https://developers.notion.com/guides/get-started/authorization#internal-connection-auth-flow-set-up) and create an internal connection for your workspace.
2. Start with read-content capability only and record the chosen page/database IDs.
3. On each permitted Notion page, use **••• → Add connections** to share that page with the integration. Having a token alone does not authorize every page.
4. When Mavo's Notion connector is implemented, enter the internal token into its masked Resources form. That form must save it to DPAPI and restrict the selected pages again per Crew. There is no such token field in the current build.

For a public one-click integration, the publisher must implement Notion's public OAuth connection and secure server-side credential handling; end users should consent/select pages rather than send tokens to the publisher. Mavo's current Google token broker is Google-only and cannot exchange Notion credentials. [Notion authorization and page sharing](https://developers.notion.com/guides/get-started/authorization).

## Telegram — future adapter

For an initial personal bot:

1. Open the verified **@BotFather** in Telegram and create a bot using `/newbot`.
2. Keep its bot token private. Open your new bot yourself and send `/start`.
3. The future connector must explicitly pair an allowed chat/user, validate updates, store its cursor, and separately authorize receiving commands versus sending messages. A bot token is not permission to read the user's complete personal Telegram inbox.
4. Enter the token only into the future masked Telegram connection form—not a public README, source file, browser URL or chat here. Do not grant group administrator privileges just for reminders.

For one shared public Mavo bot, its token stays on the publisher server, and each user needs a verified pairing flow. Local polling works only while Mavo is running; hosted delivery requires a deployed webhook/polling service. None of these Telegram flows are implemented yet. [BotFather setup](https://core.telegram.org/bots/features#botfather), [Telegram Bot API](https://core.telegram.org/bots/api).

## Public feeds — working, no key

Open **Resources → Public feeds → Choose feeds**. Enable **arXiv · Artificial Intelligence** and/or **BBC · Technology**, then Save. Grant each Crew its specific feed in the setup access block. Example: “Every morning review the AI research feed and briefly highlight relevant new titles.” Choose an interval/daily review and a model route; Save then Run. Feed access itself costs no API-key credits, but the chosen model may charge for its reviews.

Only reviewed, fixed HTTPS feed endpoints are supported. Reads use a 15-minute shared cache, 15-second timeout, 2 MB XML limit, no redirects/entities/DTD, stable-item deduplication and up to 20 titles, dates and links. Full articles, attachments and arbitrary URLs are not fetched. Feed contents remain untrusted. Disabling a feed removes its Crew grants, clears checkpoints and stops affected workers; other providers remain connected. Existing historical work receipts can retain previously collected titles. Publisher copyright/redistribution rules still apply; this is not permission to republish full articles. User-entered feeds require additional DNS-pinning/SSRF controls and remain unsupported.

## Weather and local tools — working

Weather/location use resource switches, not a user API key in the current Open-Meteo adapter. Device Location additionally requires Windows location permission; destination weather does not. Open-Meteo's free hosted API is for non-commercial use—arrange a compliant commercial endpoint/license before commercial deployment. [Provider terms](https://open-meteo.com/en/pricing).

Computer counters, reminders, Mavo's separate browser, diagnostic Terminal and owned Windows controls use explicit resource/action access, not Google or GitHub credentials. Browser sign-in happens manually in the Crew's separate profile; never export/import personal passwords or cookies for automation.

## Before adding any key

Confirm that the connector is implemented, choose the smallest supported permissions, set an expiry where possible, select resources and Crew scopes, and check actual outcomes. No model may register executable plugins, broaden permissions or read stored keys. [Developer provider/action guide](../providers/README.md), [available integrations](README.md).
