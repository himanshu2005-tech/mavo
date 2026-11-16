# 0035 — Reference-bound resource reports

Status: accepted

## Context

A news worker can read valid RSS metadata but reconstruct one URL incorrectly.
Rejecting every supported story for that copying error is unnecessarily brittle.
Accepting unverified model prose or disabling provenance checks would hide errors.

## Decision

Extend the existing WorkCheckpoint/receipt/verifier path, not the orchestration
engine. Assign item references to copied connector snapshots; resolve typed selections
in application code. Keep legacy citation support and strict current-access/scope
checks. Build feed reports only from supported records, with explicit partial status
when a sibling result is rejected. Commit reported-item deduplication with the existing
parent completion/outbox transaction. No new provider, process or SQL migration.

Follow-up: when a feed-only, non-review assignment still selects no supported items
after its one budgeted repair, degrade to application-selected fetched headlines.
Label the lack of AI analysis explicitly, discard model-written prose, and retain
normal outcome verification, current permissions and transactional delivery. Do not
use this fallback for other resources, reviewers, actions or unavailable feeds.

## Consequences

Models choose items rather than reproduce source metadata. Unsupported records do
not leak through otherwise valid summaries. Explicit resource failure and invalid
non-feed output still fail safely with the previous report retained. Feed output is a provenance-
bound headline digest, not independently fact-checked article synthesis. Resource-
specific account consent, tool policies and non-document outcome checks are unchanged.

Implementation and test boundaries: [source reporting](../source-reporting.md).
