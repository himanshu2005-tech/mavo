# Mavo's shared coordinator

Normal chat and natural-language Crew setup use a publisher-paid AIRouter Minimax model. Agent jobs use the user's separately saved model connections. The desktop never receives the shared key. There is no fallback that bills the user's agent key for normal chat.

## Local Windows testing

Copy `.env.example` to `.env` in this directory. Set `MAVO_PUBLISHER_FIREBASE_PROJECT_ID` to the same Firebase project as the desktop login and `MAVO_PUBLISHER_DATA_DIR=D:/DevTools/Mavo/publisher`. Leave `MAVO_PUBLISHER_API_KEY` empty to use Windows DPAPI storage. From the repository root:

```powershell
.\scripts\publisher.ps1 -SetKey
.\scripts\publisher.ps1
```

The key prompt is hidden. The service binds only to `127.0.0.1:8741`. Keep it running in a separate terminal. Set `MAVO_CORE_GATEWAY_URL=http://127.0.0.1:8741` in the root `.env`, then start Mavo with `.\scripts\dev.ps1`. The key already stored on this development machine does not need to be entered again.

Localhost reaches this computer only. Other people's installations need the public deployment below. The desktop cannot run jobs while it is off or suspended.

## Public HTTPS deployment

Use a server with Docker Compose and a domain whose DNS points to that server. Open ports 80 and 443. On the server, create `publisher/.env` from the example, set the Firebase project, actual `MAVO_CORE_HOST`, and server-only `MAVO_PUBLISHER_API_KEY` (or inject that environment variable from your deployment's secret manager). Restrict permissions on this file. Never copy this server configuration into a desktop build.

```sh
docker compose --env-file publisher/.env -f publisher/compose.yaml up -d --build
```

Caddy obtains TLS certificates and proxies to the private gateway container. Only the HTTPS proxy publishes a port. Use a single gateway replica and the persistent `gateway-data` volume; SQLite quota accounting must survive restarts. Back up that volume. Do not use ephemeral storage or run several replicas against separate databases. PostgreSQL/shared quota storage is required before scaling replicas.

Set `MAVO_CORE_GATEWAY_URL=https://your-core-domain` in the desktop publisher build environment. `npm run tauri build` runs a release guard that rejects a loopback coordinator URL or missing Firebase/Google publisher identity. Only the public URL, Firebase Web config, and public desktop OAuth client ID are bundled. Configure and verify Google consent separately as described in `docs/google-setup.md` and `docs/firebase-setup.md`.

The container recipes are provided for deployment; Docker and an external domain have not been provisioned by this repository. Validate TLS and a signed-in end-to-end request on your server before releasing an installer.

## Authentication, budgets, and data

Every model request requires a current Firebase ID token from verified Google sign-in, with the configured project as audience and issuer. Requests do not accept a client-selected model, provider key, system prompt, or tool execution. The server validates structured decisions and sends only planning responses. Permissions and execution remain in the local backend.

The example configuration limits usage to 50 requests/day per account and 500/day globally. Set these explicitly before deploying: a value of `0` disables the corresponding daily limit. Additional controls include per-account request rates, four concurrent provider requests, and 4096 output tokens per turn. Failed provider attempts consume a reservation, preventing retry storms from bypassing quotas. Request IDs are bound to an account and exact payload. Responses are cached for ten minutes for idempotent retry, cleared after expiry, and metadata is pruned after seven days. Account IDs are hashed in the quota ledger. Access logs are disabled. Monitor provider spend and configure a provider-side spending cap as well; account quotas do not prevent account farming.

The coordinator explicitly uses `reasoning_effort=low` and JSON mode. MiniMax M3.1 Flash otherwise defaults to maximum thinking, which can exceed an interactive chat timeout; see [MiniMax thinking-depth documentation](https://platform.minimax.io/docs/guides/text-generation). AIRouter forwards supported request options to the selected upstream provider; see [its chat API](https://www.airouter.in/docs/api-reference/chat-completions). Schema validation remains mandatory after generation. No automatic paid retry is made after a timeout or invalid response.

Planning sends the user's conversation, current setup, connection names/model IDs, connected capability names, and Crew names/responsibilities to the shared model. It does not send agent keys, inbox bodies, Google refresh tokens, or agent checkpoints. Private health/wellbeing Crew descriptions are omitted from the general index; explicitly editing one sends its requested setup text. Tell users about cloud processing in the release privacy notice. The publisher model's retention is subject to AIRouter and its upstream provider.

`GET /health` exposes only availability and model ID. It cannot prove model credit availability; a valid live request does. Never log authorization headers or raw upstream error responses.

## Google connection token exchange

Packaged Mavo cannot read a developer's root `.env` and never bundles its OAuth credential. Configure `MAVO_PUBLISHER_GOOGLE_CLIENT_ID` and `MAVO_PUBLISHER_GOOGLE_CLIENT_SECRET` on this service, using the existing **Desktop app** registration. Local development also reads root `GOOGLE_CLIENT_ID/SECRET` aliases; blank example fields do not override valid aliases. Restart the service and confirm `/health` reports `google_oauth_configured: true`. This is independent from AIRouter key availability.

`POST /v1/google/token` exchanges a one-use authorization code plus PKCE, or refreshes using a private refresh token, against Google's fixed token endpoint. It cannot require Firebase before Firebase identity has been bootstrapped. Only configured client IDs, exact native loopback redirects and supported scopes are accepted. Browser-origin requests are rejected; JSON body length is bounded; credential inputs never appear in validation errors. Default limits are 30 attempts/minute and 2,000/day per hashed source address, 20,000/day globally and four concurrent exchanges. Configure trusted reverse-proxy source addresses correctly, enforce request limits at the HTTPS proxy too, and monitor abuse. These bootstrap bounds do not prove a caller is an official installed binary.

Google tokens pass through the publisher only for exchange/refresh; no user token, code, verifier or raw response body is persisted in its database/logs. Responses use `Cache-Control: no-store`. The desktop independently verifies identity signatures, issuer/audience/nonce and state, and stores refresh credentials with Windows DPAPI. Resource reads and tool execution stay local. Public privacy notices must disclose this transient credential handling and any separately selected cloud-model data processing.

If Firebase identity uses a separate Desktop client, also configure `MAVO_PUBLISHER_FIREBASE_GOOGLE_CLIENT_ID/SECRET` on this service. Never use an arbitrary client ID supplied by a request to choose a credential, accept arbitrary token URLs, or expose the publisher's client credential to the desktop. Google application verification and an actual public HTTPS deployment remain release prerequisites.
