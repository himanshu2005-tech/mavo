# 0038 - Event-bound mail decisions and verified change delivery

Status: accepted

## Context

A Gmail domain event reached a persistent Crew, but the member adapter was a manual
worker. The event appeared only in instruction text; its Google reader used a general
inbox window. A deliverable-only `Nothing new` answer could consume the event without
classifying it. In addition, a completed composed event cycle inherited the ordinary
30-minute review interval. Scheduled inbox collection bounded the window before
deduplication, potentially starving new-to-Mavo items beyond its first ten records.

## Decision

Keep the existing TaskRun, Crew member adapters, shared GoogleResourcePool and durable
ReminderEvent outbox. Do not introduce another queue, model process or broad permission
inheritance.

- Bind a member's Google event inputs to its active TaskRun and the parent's committed
  `pending_google` batch. Check assignment, generation, account and read access. Prompt
  text or an arbitrary user-supplied plan is not an authority for this binding.
- Supply that batch to the existing Google snapshot/body path. The parent alone owns
  event acknowledgement. Poll the shared resource pool at a 60-second cadence after
  event work; an ordinary interval Crew retains its configured schedule. Polling without
  matching domain events spends no model calls. This is polling, not push delivery.
- For Gmail-triggered work, scheduled read-only inbox triage and Calendar/Tasks-only
  planners reading new mail, require one typed `mail_decisions` entry per supplied mail
  on a final checkpoint. Entries include message ID, strict boolean importance, reason
  and summary. Missing/unknown/duplicate IDs get the existing single budgeted repair;
  failure does not acknowledge the event or mark the batch seen. General mail editing
  and unrelated tool workflows are not forced to produce importance classifications.
- Generate read-only important-mail reports from those decisions plus actual supplied
  sender/subject fields. Apply existing desktop permission and outbox idempotence; do not
  depend on an optional model notification field. Record negative decisions without
  desktop spam. A valid classification is auditable, not a guarantee of model judgement.
- Filter seen Gmail IDs before bounding, deduplicate within the batch, and commit only
  supplied IDs after the parent succeeds. Failed downstream work and pending questions
  do not discard mail. Revalidate required member capabilities and account identity after
  inference; revocation cannot turn a retried task into a successful model-only answer.
- A parent with desktop permission may deliver confirmed Calendar/Tasks changes from
  completed action receipts for its current assignment, account and generation. The
  specialist need not independently publish notifications. Previews, proposals, failed
  writes, stale receipts and model claims do not qualify. This grants no new action access.
- Extract plain text or, when necessary, locally parsed HTML text from bounded MIME
  parts. Never fetch attachments, remote images, links, scripts or styles. Expose missing
  body text explicitly rather than certifying an unreadable body as read.
- Malformed weather geocoding and Google list/header payloads raise named resource
  errors; they must not appear to be successful empty resources.

## Compatibility and limits

No database migration or saved Crew/account/route change is required. Versioned
configuration, action validation, setup previews, loopback authentication and DPAPI
credentials remain in place. New checkpoint fields default to an empty list for legacy
non-mail work. Existing already-consumed events are not automatically replayed: resetting
cursors or replaying effectful planners could duplicate user work.

Gmail snapshots contain a rolling 20-header window; model input is bounded to ten unseen
headers, with up to three authorized bodies per step. Event batches contain up to three
events and are not restricted to the current inbox window. These bounds are not full
mailbox indexing. API access does not prove a plan was executed or that all mail was found.
Ambiguous dates/durations need human input, not invented Calendar events. Provider outages,
OAuth revocation, sleeping devices and exhausted budgets can still delay work.

## Verification

`test_mail_event_delivery.py` exercises the actual TaskRunner/member/outbox path using
isolated databases and mock models: exact event binding, scheduled batching, one repair,
quiet decisions, missing desktop grant, revocation, account changes, retained pending events
and duplicate prevention. `test_action_delivery.py` checks receipt eligibility and durable
delivery without specialist notification grants. `test_resource_robustness.py` covers
HTML/plain MIME, malformed bodies/headers/lists, Contacts normalization, Calendar/Tasks
pagination ceilings and weather provider failures. These are simulations, not live Google
write tests or proof that every model will prioritize every message correctly.
