# Connect GitHub to Mavo

This connects repositories as resources for Crew. It is separate from MAVO CORE chat, model API keys and Google sign-in. You do not need a Google Workspace connection to use GitHub.

## Normal user connection

Open **Resources → GitHub → Connect GitHub**. Enter the displayed code on GitHub, check the app name and authorize. If installation is needed, Mavo opens GitHub for repository selection and finishes automatically. No keys, scope matrix, Refresh or Save step is required in the normal flow. If more than ten repositories were shared, choose up to ten in Mavo. [User guide and Revoke access](connecting-accounts.md).

The publisher registers **one** GitHub App for everyone; see [GitHub App setup](github-app-setup.md). Missing publisher configuration leaves browser sign-in honestly unavailable. Installing an app is not the same as granting every Crew access. Give each Crew only its needed capabilities and a model route, then Run. User authorization, selected repositories and Actions policies are independently enforced. Tokens are refreshed locally and stored using DPAPI.

## Advanced token fallback

The remaining token instructions describe the developer API fallback and older connections, not the normal Resources UI. Token forms have been removed from the consumer screen. Developer clients can call authenticated `POST /connectors/github/connect` with `token`, `repositories` and `connector_capabilities`. Public users should use the browser connection above.

For Google Drive, Contacts (People API), keyless public feeds and future Notion/Telegram setup, see the [all-provider setup README](plugins/SETUP.md). Those connections do not require a GitHub token or grant access to your repositories.

## 1. Create a limited token

1. Open [GitHub fine-grained token settings](https://github.com/settings/personal-access-tokens).
2. Select **Generate new token**, name it `Mavo`, and choose an expiry.
3. Choose the repository owner. Under **Repository access**, select **Only select repositories** and the repositories you want Mavo to use.
4. Under **Repository permissions**, keep **Metadata: Read-only**. Add only the permissions you need:

   - Issue monitoring: **Issues → Read-only**.
   - Issue creation/comments: **Issues → Read and write**.
   - Pull-request monitoring: **Pull requests → Read-only**.
   - Workflow/CI status monitoring: **Actions → Read-only**.

5. Generate and copy the token. If an organization requires approval, wait for its administrator to approve it before connecting.

Do not grant repository administration, Contents write, Actions write or broad account access for these features. Never send your token in chat, paste it into documentation or commit it. [GitHub token guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

## 2. Connect inside Mavo

1. Use a local developer API client against authenticated `POST /api/v1/connectors/github/connect`; no token form is shown in Resources.
2. Supply the token only in the authenticated API's `token` field. It is stored using Windows DPAPI, not plain SQLite or browser storage. No consumer token form remains.
3. Supply repositories as exact `owner/repository` strings. For example:

   ```text
   your-name/college-project
   your-name/personal-site
   ```

4. Select the matching Mavo capabilities: repository metadata, issues, pull requests, workflow/CI status and/or issue writes. Pull-request and CI access are separate opt-ins; existing connections do not receive them automatically.
5. Submit the typed connection payload. Mavo checks account identity and selected repository access. Individual permissions remain enforced by GitHub on actual tool use; success is not proof of every selected permission.

The token's repository selection and Mavo's selection must both include the target. You cannot gain additional access by selecting a checkbox in Mavo. To replace access or rotate an expiring token, open **Access**, enter the updated token/repository selection and save. An account change requires disconnection first.

## 3. Give a Crew access

In Chat or Create Crew, describe the responsibility. For example:

> Check my selected GitHub repositories every 30 minutes. Tell me about new open pull requests and failed workflow runs. Include links. Do not modify code, merge, comment, or rerun anything. Avoid repeating notifications for the same PR or run.

Answer the setup questions, select your **agent model route**, then review the proposed repository/PR/CI permissions. Select **Verify & add** only when ready. You can also configure these grants using the Crew editor's **Connected account permissions**.

Reading PR/CI metadata does not need an issue-write Action enabled. If you do want writes, separately enable **Actions → GitHub → Create issue / Comment on issue** for that Crew, preferably restricted to exact repositories. Even unrestricted custom action policies cannot escape the connection's selected repositories.

## What works, and what does not

- Repository metadata; issue headers; open pull-request metadata; recent workflow-run status, conclusion, branch and links.
- Bounded reads: up to 20 issues/open PRs/recent workflow runs per selected repository, at most 10 repositories. Authorized Crew share a 60-second cache.
- Interval reviews, not GitHub webhooks. Mavo and Windows must stay awake. Notifications require the Crew's desktop-notification grant and available model-call budget.
- Create issue/comment are the only GitHub writes implemented. No PR creation/merge/review, source modification, code execution, workflow rerun/cancellation, log/artifact downloads or full historical sync.
- Model-selected summaries and repeat suppression are bounded agent behavior, not a guarantee that every event in a busy repository will be observed. Tune intervals/budgets and use GitHub's own notifications for critical monitoring.
- GitHub App browser authorization uses the standard device flow and requires the publisher's one-time app registration. End users do not supply personal tokens. Advanced token connections remain supported; no shared publisher access token or private key is embedded.

[PR API permission requirements](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests), [workflow-run API permissions](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-repository).

## Troubleshooting

- **401 / expired token:** create or rotate the token, then reconnect.
- **403:** check the exact feature's token permission, organization policy/approval and repository selection. A rejected capability is removed from available access until reconnection; affected Crew stop safely.
- **404 / repository not found:** check `owner/repository`, private-repository access and the selected resource owner. GitHub can conceal private repositories from unauthorized tokens.
- **No PR/CI snapshots:** enable that capability both on the GitHub connection and on the Crew. Issue read access is not PR or CI access.
- **No notification:** check Run status, interval, daily model budget and desktop-notification permission. A quiet cycle does not necessarily indicate a failure; inspect Latest work.
- **429:** wait for the rate limit. Do not repeatedly reconnect or retry a write with an uncertain outcome.

Disconnecting GitHub deletes its local token/cache, removes GitHub Crew grants, fences affected work and redacts derived results. It does not revoke the token on GitHub; delete it in GitHub settings if it is no longer needed. Other connections and saved chats remain intact.

## Developers: adding more resources/actions

Read the [provider/action SDK guide](providers/README.md) and [plugin guide](plugins/README.md). Main files: `connectors/catalog.py`, `connectors/hub.py`, `connectors/actions.py`, `crew_workers.py`, and `ConnectorAccounts.tsx`. Contract tests use temporary databases and HTTP fixtures; they do not connect a real GitHub account or create real issues.
