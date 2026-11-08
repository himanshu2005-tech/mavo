# Connect your accounts

Open **Resources**. Each account has one Connect button and a plain-language access summary.

## Google

Click **Connect Google**, choose your account and review Google's consent screen. Return to Mavo: the card changes to **Connected** automatically. Expand **What Mavo can access** to see the permissions actually granted, including partial consent.

Mavo requests Gmail read/send/organization, Calendar and Tasks read/write, Drive **file metadata only**, and saved Contacts **read-only**. It cannot read document contents or edit contacts through these connectors. Each Crew still needs its own resource permissions; changes follow your saved Actions settings.

## GitHub

Click **Connect GitHub**. Enter the displayed one-time code on GitHub and authorize Mavo. If GitHub asks you to install the app, choose the repositories to share there. Mavo detects the authorized selection and changes to **Connected** automatically—no API keys, permission matrix, Refresh list or Save connection step in Mavo.

GitHub requires that short device code; Mavo cannot bypass its consent screen. If more than ten repositories were shared, Mavo asks you to choose up to ten rather than silently selecting some. Supported access is repository metadata, issues, open PRs, CI status and policy-controlled issue/comment creation. No code editing, merging, workflow execution or repository deletion. Only capabilities granted by GitHub and saved Crew permissions can be used.

## Revoke access

Use **Revoke access** on a connected card. Mavo removes saved credentials, stops affected Crew and removes their account access immediately.

- Google: Mavo calls Google's revocation endpoint and reports whether Google confirmed removal. If that call fails, local access is still removed and Mavo links to Google's connections page to finish.
- GitHub: local access is removed immediately; Mavo opens **Authorized GitHub Apps** so you can revoke its remote authorization. This secretless desktop flow cannot silently revoke GitHub's app grant. User confirmation is recorded separately from API verification, never presented as a verified provider receipt.

Revoking a resource connection does not delete cloud emails, files, repositories or your Mavo account. Google Workspace revocation is separate from Firebase login. Credentials and synced local snapshots/results are removed as applicable; user-entered chats and Crew descriptions remain. Reconnecting never restores a Crew's removed permissions automatically.

Publisher setup is separate from user connection: [Google](google-setup.md), [GitHub App](github-app-setup.md). Public users should never register applications or send their keys to the publisher.
