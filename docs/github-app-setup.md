# Connect every user's GitHub account to Mavo

This is **publisher setup**, performed once by the person releasing Mavo. Public users click **Resources → GitHub → Connect GitHub**, enter the short code on GitHub and authorize. If installation is needed, Mavo opens GitHub for repository selection and detects completion automatically. With up to ten shared repositories, there are no extra forms, Refresh or Save steps in Mavo. [Simple user guide and revocation](connecting-accounts.md). App installation and user authorization remain separate GitHub steps.

## Register Mavo once

1. Open [GitHub Settings → Developer settings → GitHub Apps](https://github.com/settings/apps) → **New GitHub App**. A personal account is enough for initial publisher testing.
2. Choose a unique name (for example `Mavo Assistant Test`) and a real homepage/support URL you control. After creation, copy its URL slug from `https://github.com/apps/<slug>`.
3. Enable **Device Flow** and keep expiring user access tokens enabled. No OAuth client secret, private key, hosted callback or local redirect is needed for this native flow. Leave callback/setup URLs empty where optional; do not enable “Request user authorization (OAuth) during installation.”
4. Uncheck **Active** under Webhooks. The Webhook URL is required only while Active is selected; leave URL/Secret blank after disabling it. Do not enter a placeholder URL or buy hosting for this release. Mavo currently performs bounded interval reviews, not hosted GitHub webhooks. [GitHub's registration instructions](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).
5. Repository permissions: **Metadata: Read-only**, **Issues: Read-only**, **Pull requests: Read-only**, **Actions: Read-only**. For Mavo's existing issue/comment actions, use **Issues: Read and write** instead; Mavo still requires independent user/Crew write policies. Do not grant Contents write, Administration, Actions write, organization membership or account email permissions.
6. Under installation availability, choose **Any account** for a public product. Organizations may require administrator approval. “Only this account” restricts testing to the app owner.
7. Create the app. Copy its **Client ID** (not the numeric App ID) and slug. Do not generate/send a private key or client secret for this flow.

Add these **public values** to the project-root `.env`:

```dotenv
MAVO_GITHUB_CLIENT_ID=your-github-app-client-id
MAVO_GITHUB_APP_SLUG=your-github-app-slug
```

Restart source development with `scripts\dev.ps1 -Restart`. For the packaged desktop, run `scripts\package-backend.ps1` and rebuild/restart: its public `publisher.json` includes the ID/slug automatically. End users never enter them. Existing Google/Firebase configuration and the publisher's Google broker are unchanged. No GitHub client secret or user token is bundled.

## Access and limits

GitHub user tokens are limited by both the user's rights and the app installation. Mavo lists up to 100 repository choices across up to five installations and saves up to ten explicitly selected repositories. Offered capabilities are the selected installations' common permissions; GitHub additionally enforces the user's own rights on each request. Crew need their own exact read/write grants, and writes need matching saved Actions policies. Models cannot expand those limits.

The selected-repository set is currently shared by the connection. Per-Crew grants restrict capabilities across that set; they are not separate per-Crew repository selectors. Do not connect unrelated confidential repositories expecting folder/repository isolation within one read grant.

Implemented resources: repository metadata, recent issues, open pull requests and CI run status. Implemented writes: create issues/comments. Source edits, merges, workflow reruns, logs/artifacts, secrets and arbitrary account access are not implemented. Connecting alone does not start Crew or enable every action. GitHub operations are attributed to the authorized user.

Device codes and uncommitted tokens stay in memory; only the short user code reaches the UI. Access/refresh tokens are DPAPI-protected outside SQLite/prompts after explicit repository review. Pending authorization cancels/expires without replacing saved access. Refresh is serialized and rotates credentials. Logout/quit cancel pending sign-in; disconnect also removes local credentials and stops affected work. Disconnect does not remotely uninstall the app; users can revoke it in GitHub's Installed/Authorized GitHub Apps settings. Provider revocation/401/403 stops affected work, never broadens access or switches accounts.

## Verify

Click Connect GitHub, enter Mavo's code **only on `https://github.com/login/device`**, and confirm the actual app name. Install Mavo on selected test repositories if needed; Mavo connects automatically. If more than ten repositories are shared, choose up to ten in the exceptional selection screen. Then create a Crew: “Every hour report newly failed CI runs.” Grant workflow read and desktop updates, choose an agent route and Run. AI calls use that route's credits; this does not buy GitHub Actions minutes or paid GitHub features.

- **Unavailable:** missing publisher client ID/slug; source configuration needs restart, bundles need repackaging.
- **Device flow disabled:** enable it on the registered GitHub App.
- **Empty repositories:** install on the correct account, select repositories, approve organization access, then refresh.
- **Permission missing:** update the app/installation permissions on GitHub, then review Mavo access again. Mavo cannot override GitHub.
- **Expired/declined:** restart authorization; the earlier saved connection is preserved.
- **Different account:** disconnect the old GitHub account first. GitHub identity can differ from Google app login.

Automated tests use synthetic tokens/HTTP fixtures. A real connection requires this one-time publisher registration and user authorization; a passing test does not fabricate them. [Advanced token setup](github-setup.md#advanced-token-fallback) remains optional for developers.

Primary references: [GitHub App device flow](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app#using-the-device-flow-to-generate-a-user-access-token), [refresh without a secret for device tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens), [installation access](https://docs.github.com/en/rest/apps/installations).
