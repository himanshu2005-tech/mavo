# Mavo integrations and resource plugins

In this release, a plugin means a built-in, reviewed resource adapter or typed action—not arbitrary downloaded code. Codex/ChatGPT plugins are not automatically installable inside Mavo.

For all requested provider keys, publisher versus user setup, and honest implementation status, start with [Provider keys and setup](SETUP.md).

## Available now

- Google Workspace: Gmail, primary Calendar, default Tasks, **Drive metadata** and **Contacts (People API)**. Drive/Contacts are read-only and opt-in. [Setup and cost limits](SETUP.md#drive-and-contacts--read-only-plugins).
- GitHub: selected repositories/issues, **open pull requests**, and **workflow/CI status**. Issue creation/comments are separately controlled writes. [Setup](../github-setup.md).
- Weather: explicit destination forecasts, independently of device Location permission. [Resource setup](../providers/README.md).
- Computer metrics, deterministic reminders, separate per-Crew browser profiles, limited diagnostic Terminal and owned Windows tools. Each has explicit access controls.
- Public feeds: arXiv AI and BBC Technology titles/dates/links. No key; exact per-feed Crew permissions, 15-minute caching and bounded safe XML reads. [Setup](SETUP.md#public-feeds--working-no-key).

The new GitHub read plugins use `github:pulls:read` and `github:workflows:read`. They do not grant merge, source-code write, workflow execution, job-log download or artifact access. Connection and per-Crew grants are both required. Disconnected-provider write actions remain hidden.

## Useful next candidates—not connected or implemented yet

- **Google Docs/Sheets contents and writes:** not supplied by Drive metadata access. Needs selected-file authorization and dedicated typed tools. Contacts editing is also not implemented.
- **Notion:** selected-page knowledge and project responsibilities. A public product needs the provider's public authorization flow and explicit page selection; a token alone does not grant access to every page. [Notion authorization](https://developers.notion.com/guides/get-started/authorization).
- **Telegram:** a user-authorized notification bot and scoped command inbox, not access to the user's entire private Telegram account. Bot tokens must stay in secure storage and chat IDs must be explicitly paired. [Telegram Bot API](https://core.telegram.org/bots/api).
- **User-entered RSS/Atom feeds:** needs destination/DNS-pinning controls before arbitrary URLs are supported; curated feeds are available now.

These are roadmap candidates, not functioning connection buttons. Choose a concrete responsibility before implementing one. No provider subscription, third-party app install, new account access or write permission is enabled automatically.

## Add a reviewed plugin

Follow [the provider/action SDK](../providers/README.md#add-another-provider): server-owned namespaced capabilities; fixed-authority bounded HTTP reads; secure credentials; real connection checks; cached, untrusted snapshots; exact per-Crew grants; deterministic scheduling; typed and policy-fenced writes; provider-scoped revocation; and fault tests. The LLM may propose a known capability, never register executable integrations or grant access.

For every integration, document token/OAuth setup, actual permissions, read/write distinction, limits, privacy/model data flow, disconnection and verified outcomes. Publish a maintained README and tests with it.
