# 0034 — Verify setup using the real runtime before activation

Status: accepted (developer preview)

## Context

Configuration validation and opening a website do not establish that a Crew can execute its
assigned workflow. Lookup continuations also discarded useful results and misreported a local
round limit as model-budget exhaustion. Increasing global limits would not correct either issue.

## Decision

Setup drafts require a bounded first-run result before publication. Private read-only adapters
reuse CrewWorkerRuntime, CrewComposition, TaskRunner and existing policy/source verification.
They cannot be submitted through public task APIs. The first run is charged to the chosen model
routes and the draft's daily budget; unused reservations are refunded. Notifications and all
consequential effects are disabled. Unsupported effectful verification stays blocked rather
than being reported as tested. Reminders validate scheduling without firing early.

Measure actual model calls per specialist, including repair and follow-up calls, then configure
n + 3 within the approved individual/shared/daily ceilings. Do not interpret a model's estimate
of steps as a budget measurement. The scheduler cannot activate an untested draft or overwrite
its in-flight verification. Interrupted tests do not restart paid work automatically.

Public lookup receipts are persisted as untrusted assignment-scoped context before synthesis.
Deduplicate canonical requests, force final synthesis on repeats, retain partial results, and
distinguish source failure, loop limits and budget exhaustion. Feed snapshots can support source
verification; missing quotations are not disguised as a user question. Empty successfully read
feeds have a deterministic quiet outcome, not fabricated news. Collection does not mark items
handled until the Crew successfully produces its consolidated result.

## Consequences

New Chat/builder additions take longer and may consume user model credits. Some workflows remain
drafts pending better configuration, sufficient budget, sign-in, or future supervised effectful
verification. Existing activated/imported Crew and the legacy direct creation API remain
compatible. No schema migration or data reset is required. One successful sample does not prove
arbitrary future outcomes; real-account/provider release testing remains necessary.
