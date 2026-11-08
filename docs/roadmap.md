# Roadmap

## Milestone 1 — desktop foundation

Implemented: Tauri/React shell, managed FastAPI sidecar, authenticated loopback API, SQLite/Alembic persistence, model routes, DPAPI credentials, chat, logging, and tests. User-requested features brought forward include 15 Crew types, Run/Stop workers, checkpoints, budgets, crash recovery, Activity, reminders, Computer metrics, description-first Crew proposals, policy-controlled Google actions, and required Firebase Google login. General Signals rules and complete account synchronization remain unfinished.

## Milestone 2 — Resource Pool

Build on the adapter protocol, explicit weather-location selection, opt-in device metrics, and user-requested Google read adapters by adding a complete registry, freshness policies, Windows diagnostics, local calendar, and local tasks. Publisher OAuth setup is documented in [google-setup.md](google-setup.md).

## Milestone 3 — Signals

Add deterministic time/condition/resource-event rules, persisted rule state, deduplication keys, cooldowns, hysteresis, retries, and a leased SQLite job queue with crash recovery.

## Milestone 4 — Crew Engine

Build on the 15 manifests, Run/Stop workers, checkpoints, real Computer monitoring, and daily Schedule reminders by adding richer scoped memory, resource subscriptions, and approved action tools. Text workers do not imply account integrations.

## Milestone 5 — Goals

Add checkpoints, dependencies, bounded proactive sessions, continuous-work budgets, stop conditions, and restart recovery.

## Milestone 6 — integrations

Native Google PKCE sign-in, publisher client packaging, bounded Gmail/Calendar/Tasks reads and policy-controlled writes, and Firebase account login were brought forward at user request. Complete publisher verification and real-account write release testing, then expand synchronization coverage. Native clients re-consent to the selected scope union; they do not support web incremental authorization. Add WeatherAPI.com using user/development credentials and GitHub metadata/issue/CI signals with GitHub App architecture.

Additional adapters: opt-in Drive metadata and saved Contacts, GitHub repository/issue/PR/CI reads, and curated public feeds. GitHub App native device connection is implemented with one publisher registration, selected-repository review and expiring-token refresh; personal tokens remain an advanced fallback. Real sign-in needs publisher configuration and user consent. These are bounded scheduled reads, not full sync/webhooks. Google document contents/Docs/Sheets editing, Contacts writes, Notion, Telegram and arbitrary RSS URLs remain future work. [Provider setup and limits](plugins/SETUP.md).

## Milestone 7 — advanced capabilities

Publisher-funded Minimax planning, automatic Crew creation/refinement, explicit setup access, and durable policy-controlled actions with verified outcomes were brought forward. Add Coding Crew workflows, richer activity history, and a no-op/free entitlement implementation ready for future subscription providers.

Mavo can only work while the computer and runtime are running. Hosted background execution is a separate future capability.

Before public release: deploy the publisher gateway on HTTPS with persistent quota storage, validate Firebase account isolation and Google consent for public users, establish provider spending limits, publish the cloud-data privacy notice, and sign the Windows installer. Local coordinator testing does not provide a public service.
